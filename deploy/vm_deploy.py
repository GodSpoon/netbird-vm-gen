#!/usr/bin/env python3
"""VM Deploy Tool — main CLI entry point.

Orchestrates the full VM-deployment workflow:
1. Collect configuration (CLI args or interactive wizard).
2. Authenticate with NinjaOne (if credentials supplied).
3. Generate NetBird and NinjaOne setup scripts.
4. Render cloud-init autoinstall YAML.
5. Build a cidata ISO.
6. Deploy to VMware or Hyper-V.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Allow imports when script is run directly: python deploy/vm_deploy.py
_deploy_dir = Path(__file__).parent
_project_root = _deploy_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deploy.lib.config_builder import hash_password, render_autoinstall, render_script
from deploy.lib.ninjaone_client import (
    NinjaOneAPIError,
    NinjaOneAuthError,
    NinjaOneClient,
)
from deploy.lib.netbird_installer import generate_setup_script
from deploy.lib.prompts import run_wizard
from deploy.lib import hyperv_deployer, vmware_deployer

console = Console()

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------
EXIT_SUCCESS = 0
EXIT_DEPLOY_ERROR = 1
EXIT_VALIDATION_ERROR = 2
EXIT_AUTH_ERROR = 3

# ---------------------------------------------------------------------------
# Global config loading
# ---------------------------------------------------------------------------

_GLOBAL_CONFIG_PATH = _project_root / "config.yaml"


def _load_global_config() -> dict:
    """Load global defaults from config.yaml."""
    if not _GLOBAL_CONFIG_PATH.exists():
        return {}
    with _GLOBAL_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        return {}
    flat: dict[str, Any] = {}
    defaults = data.get("defaults", {})
    for k, v in defaults.items():
        flat[k] = v
    netbird = data.get("netbird", {})
    for k, v in netbird.items():
        flat[f"netbird_{k}"] = v
    ninjaone = data.get("ninjaone", {})
    for k, v in ninjaone.items():
        if k == "regions":
            continue
        flat[f"ninjaone_{k}"] = v
    naming = data.get("naming", {})
    for k, v in naming.items():
        flat[f"naming_{k}"] = v
    return flat


# ---------------------------------------------------------------------------
# Keyring (secure credential storage)
# ---------------------------------------------------------------------------

_KEYRING_SERVICE = "netbird-vm-gen/ninjaone"


def _save_ninjaone_credentials(client_id: str, client_secret: str) -> None:
    """Store NinjaOne credentials in the OS credential store."""
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, "client_id", client_id)
        keyring.set_password(_KEYRING_SERVICE, "client_secret", client_secret)
    except Exception:
        pass


def _load_ninjaone_credentials() -> tuple[str | None, str | None]:
    """Retrieve NinjaOne credentials from the OS credential store."""
    try:
        import keyring
        client_id = keyring.get_password(_KEYRING_SERVICE, "client_id")
        client_secret = keyring.get_password(_KEYRING_SERVICE, "client_secret")
        return client_id, client_secret
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# VM naming convention
# ---------------------------------------------------------------------------

def _generate_vm_name(company: str, existing_names: list[str] | None = None) -> str:
    """Generate the next VM name following the naming pattern.

    Pattern: netbird-{company}-{index:02d}
    """
    company = re.sub(r"[^\w-]", "", company.replace(" ", "-")).lower()
    company = re.sub(r"-+", "-", company).strip("-")
    existing = existing_names or []
    existing_nums: set[int] = set()
    prefix = f"netbird-{company}-"
    for name in existing:
        if name.startswith(prefix):
            suffix = name[len(prefix):]
            if suffix.isdigit():
                existing_nums.add(int(suffix))
    idx = 1
    while idx in existing_nums:
        idx += 1
    return f"{prefix}{idx:02d}"


# ---------------------------------------------------------------------------
# Save profile
# ---------------------------------------------------------------------------

def _save_profile(path: Path, vm_config: dict) -> None:
    """Persist the current VM config as a YAML profile."""
    save_cfg = {
        k: v
        for k, v in vm_config.items()
        if k not in ("password", "password_hash", "netbird_script", "ninjaone_script")
    }
    profile: dict[str, Any] = {}
    for key in (
        "client_name",
        "hypervisor",
        "vcenter_server",
        "cluster",
        "datastore",
        "network",
    ):
        if key in save_cfg:
            profile[key] = save_cfg[key]
    if "netbird_setup_key" in save_cfg or "netbird_management_url" in save_cfg:
        profile["netbird"] = {}
        if "netbird_setup_key" in save_cfg:
            profile["netbird"]["setup_key"] = save_cfg["netbird_setup_key"]
        if "netbird_management_url" in save_cfg:
            profile["netbird"]["management_url"] = save_cfg["netbird_management_url"]
    nj_keys = [
        "ninjaone_region",
        "ninjaone_client_id",
        "ninjaone_client_secret",
        "ninjaone_org",
        "ninjaone_location",
    ]
    if any(k in save_cfg for k in nj_keys):
        profile["ninjaone"] = {}
        if "ninjaone_region" in save_cfg:
            profile["ninjaone"]["region"] = save_cfg["ninjaone_region"]
        if "ninjaone_client_id" in save_cfg:
            profile["ninjaone"]["client_id"] = save_cfg["ninjaone_client_id"]
        if "ninjaone_client_secret" in save_cfg:
            profile["ninjaone"]["client_secret"] = save_cfg["ninjaone_client_secret"]
        if "ninjaone_org" in save_cfg:
            profile["ninjaone"]["organization"] = save_cfg["ninjaone_org"]
        if "ninjaone_location" in save_cfg:
            profile["ninjaone"]["location"] = save_cfg["ninjaone_location"]
    defaults: dict[str, Any] = {}
    for src, dst in (
        ("cpu", "cpu"),
        ("ram", "memory"),
        ("disk", "disk"),
        ("username", "username"),
        ("dns_servers", "dns"),
        ("search_domain", "domain"),
        ("ssh_key", "ssh_key"),
    ):
        if src in save_cfg:
            defaults[dst] = save_cfg[src]
    if defaults:
        profile["defaults"] = defaults
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(profile, fh, default_flow_style=False, sort_keys=False)
    console.print(f"[green]Profile saved to {path}[/green]")


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vm-deploy",
        description="Deploy an Ubuntu VM with NetBird and NinjaOne pre-installed.",
    )
    parser.add_argument(
        "--profile", type=Path, help="YAML client profile to pre-fill defaults."
    )
    parser.add_argument(
        "--save-profile",
        type=Path,
        metavar="PATH",
        help="Save the resolved config as a YAML profile for future use.",
    )
    parser.add_argument(
        "--hypervisor",
        choices=["vmware", "hyperv"],
        help="Target hypervisor (required for non-interactive mode).",
    )
    parser.add_argument("--vm-name", help="Virtual-machine name.")
    parser.add_argument("--hostname", help="OS hostname.")
    parser.add_argument("--description", help="VM description / notes.")
    parser.add_argument("--cpu", type=int, help="Number of vCPUs.")
    parser.add_argument("--ram", type=int, help="RAM in megabytes.")
    parser.add_argument("--disk", type=int, help="Disk size in gigabytes.")
    parser.add_argument(
        "--ip", help="Static IP with CIDR, e.g. 192.168.1.10/24."
    )
    parser.add_argument("--gateway", help="Default gateway.")
    parser.add_argument("--dns", help="DNS server(s), comma-separated.")
    parser.add_argument("--username", help="Admin username.")
    parser.add_argument("--password", help="Admin password (will be hashed).")
    parser.add_argument(
        "--ssh-key", type=Path, help="Path to SSH public-key file (optional)."
    )
    parser.add_argument("--netbird-setup-key", help="NetBird setup key.")
    parser.add_argument(
        "--netbird-management-url",
        default="https://api.netbird.io",
        help="NetBird management URL (default: https://api.netbird.io).",
    )
    # NinjaOne
    parser.add_argument(
        "--ninjaone-region", default="US", help="NinjaOne region (default: US)."
    )
    parser.add_argument(
        "--ninjaone-base-url",
        help="Custom NinjaOne base URL (overrides region). Example: https://4eos.rmmservices.net",
    )
    parser.add_argument("--ninjaone-client-id", help="NinjaOne API client ID.")
    parser.add_argument(
        "--ninjaone-client-secret", help="NinjaOne API client secret."
    )
    parser.add_argument(
        "--ninjaone-org-id", type=int, help="NinjaOne organization ID (numeric)."
    )
    parser.add_argument(
        "--ninjaone-location-id",
        type=int,
        help="NinjaOne location ID (numeric).",
    )
    parser.add_argument(
        "--ninjaone-org-name",
        help="NinjaOne organization name (fuzzy match).",
    )
    parser.add_argument(
        "--ninjaone-location-name",
        help="NinjaOne location name (fuzzy match).",
    )
    parser.add_argument(
        "--ninjaone-org", help="NinjaOne organization (legacy: name or ID)."
    )
    parser.add_argument(
        "--ninjaone-location", help="NinjaOne location (legacy: name or ID)."
    )
    # Keyring
    parser.add_argument(
        "--save-credentials",
        action="store_true",
        help="Store NinjaOne credentials in the OS keyring after successful use.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render configs but do not deploy.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output.")
    return parser


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_ip_cidr(value: str) -> None:
    if not value or "/" not in value:
        raise ValueError("IP Address with CIDR is required (e.g. 192.168.1.10/24).")
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ValueError(f"Invalid IP/CIDR: {exc}")


def _validate_positive_int(value: int | None, name: str) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be a positive integer.")


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


_UNRESOLVED_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


def _resolve_env_vars(value):
    """Recursively resolve ${ENV_VAR} placeholders in strings/lists/dicts.

    Missing environment variables are left as-is; they will fail later
    if the value is actually used.
    """
    if isinstance(value, str):

        def replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        return _ENV_VAR_RE.sub(replacer, value)
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    return value


def _check_unresolved_placeholders(data: dict, path: str = "") -> list[str]:
    """Return a list of human-readable messages for unresolved ${VAR} placeholders."""
    errors: list[str] = []
    for key, val in data.items():
        current = f"{path}.{key}" if path else key
        if isinstance(val, str) and _UNRESOLVED_RE.search(val):
            errors.append(
                f"  {current}: unresolved placeholder '{val}' — "
                f"set the environment variable(s) before running."
            )
        elif isinstance(val, dict):
            errors.extend(_check_unresolved_placeholders(val, current))
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, str) and _UNRESOLVED_RE.search(item):
                    errors.append(
                        f"  {current}[{i}]: unresolved placeholder '{item}' — "
                        f"set the environment variable before running."
                    )
                elif isinstance(item, dict):
                    errors.extend(_check_unresolved_placeholders(item, f"{current}[{i}]"))
    return errors


def _flatten_profile(data: dict) -> dict:
    """Flatten nested profile YAML into the flat config dict used by the tool."""
    flat: dict[str, Any] = {}

    # Top-level keys — hypervisor-agnostic infrastructure
    for key in (
        "client_name",
        "hypervisor",
        # VMware
        "vcenter_server",
        "cluster",
        "datastore",
        "network",
        # Hyper-V
        "hyperv_switch",
        "vm_path",
        # Per-VM overrides (can live at top-level or in defaults)
        "vm_name",
        "hostname",
        "description",
        "ip_address",
        "gateway",
        "cpu",
        "ram",
        "disk",
        "username",
        "password",
        "ssh_key",
    ):
        if key in data:
            flat[key] = data[key]

    netbird = data.get("netbird", {})
    if "setup_key" in netbird:
        flat["netbird_setup_key"] = netbird["setup_key"]
    if "management_url" in netbird:
        flat["netbird_management_url"] = netbird["management_url"]

    ninjaone = data.get("ninjaone", {})
    for src, dst in (
        ("region", "ninjaone_region"),
        ("base_url", "ninjaone_base_url"),
        ("client_id", "ninjaone_client_id"),
        ("client_secret", "ninjaone_client_secret"),
        ("organization", "ninjaone_org"),
        ("location", "ninjaone_location"),
    ):
        if src in ninjaone:
            flat[dst] = ninjaone[src]

    defaults = data.get("defaults", {})
    # Only copy from defaults if not already set at top-level
    def _default(key: str, dst: str | None = None) -> None:
        dst = dst or key
        if dst not in flat and key in defaults:
            flat[dst] = defaults[key]

    _default("cpu")
    _default("memory", "ram")
    _default("disk")
    _default("username")
    _default("ip_address")
    _default("gateway")
    _default("dns", "dns_servers")
    _default("domain", "search_domain")
    _default("ssh_key")

    return flat


def _load_profile(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"Profile file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("Profile YAML must be a mapping.")
    data = _resolve_env_vars(data)
    unresolved = _check_unresolved_placeholders(data)
    if unresolved:
        raise ValueError(
            "Profile contains unresolved environment-variable placeholders:\n"
            + "\n".join(unresolved)
        )
    return _flatten_profile(data)


# ---------------------------------------------------------------------------
# Non-interactive config builder
# ---------------------------------------------------------------------------

def _build_config_from_args(
    args: argparse.Namespace, profile: dict | None, global_cfg: dict | None
) -> dict:
    """Merge CLI args on top of profile defaults and global config."""
    cfg: dict[str, Any] = dict(global_cfg) if global_cfg else {}
    if profile:
        for k, v in profile.items():
            if v is not None and v != "":
                cfg[k] = v

    for key, arg_val in (
        ("vm_name", args.vm_name),
        ("hostname", args.hostname),
        ("description", args.description),
        ("cpu", args.cpu),
        ("ram", args.ram),
        ("disk", args.disk),
        ("ip_address", args.ip),
        ("gateway", args.gateway),
        ("username", args.username),
        ("password", args.password),
        ("netbird_setup_key", args.netbird_setup_key),
        ("netbird_management_url", args.netbird_management_url),
        ("ninjaone_region", args.ninjaone_region),
        ("ninjaone_base_url", args.ninjaone_base_url),
        ("ninjaone_client_id", args.ninjaone_client_id),
        ("ninjaone_client_secret", args.ninjaone_client_secret),
        ("ninjaone_org_id", args.ninjaone_org_id),
        ("ninjaone_location_id", args.ninjaone_location_id),
        ("ninjaone_org_name", args.ninjaone_org_name),
        ("ninjaone_location_name", args.ninjaone_location_name),
        ("ninjaone_org", args.ninjaone_org),
        ("ninjaone_location", args.ninjaone_location),
    ):
        if arg_val is not None and arg_val != "":
            cfg[key] = arg_val

    # Try keyring if credentials still missing
    if not cfg.get("ninjaone_client_id") or not cfg.get("ninjaone_client_secret"):
        kr_id, kr_secret = _load_ninjaone_credentials()
        if kr_id and kr_secret:
            cfg.setdefault("ninjaone_client_id", kr_id)
            cfg.setdefault("ninjaone_client_secret", kr_secret)
            if args.verbose:
                console.print(
                    "[dim]Loaded NinjaOne credentials from OS keyring.[/dim]"
                )

    if args.dns is not None:
        cfg["dns_servers"] = [s.strip() for s in args.dns.split(",") if s.strip()]

    if args.ssh_key is not None:
        if not args.ssh_key.exists():
            raise ValueError(f"SSH key file not found: {args.ssh_key}")
        cfg["ssh_key"] = args.ssh_key.read_text(encoding="utf-8").strip()
    required = ["vm_name", "hostname", "ip_address", "gateway", "username", "password"]
    missing = [f for f in required if not cfg.get(f)]
    cfg["_missing_fields"] = missing

    # Only validate fields that are already present;
    # missing ones will be prompted for and validated afterwards.
    if cfg.get("ip_address"):
        _validate_ip_cidr(cfg["ip_address"])
    _validate_positive_int(cfg.get("cpu"), "CPU")
    _validate_positive_int(cfg.get("ram"), "RAM")
    _validate_positive_int(cfg.get("disk"), "Disk")

    if args.hypervisor:
        cfg["hypervisor"] = args.hypervisor

    if "cpu" in cfg and "CPU" not in cfg:
        cfg["CPU"] = cfg["cpu"]
    if "ram" in cfg and "MemoryMB" not in cfg:
        cfg["MemoryMB"] = cfg["ram"]
    if "disk" in cfg and "DiskGB" not in cfg:
        cfg["DiskGB"] = cfg["disk"]

    return cfg


# ---------------------------------------------------------------------------
# Prompt for missing required fields (semi-interactive mode)
# ---------------------------------------------------------------------------

def _prompt_for_missing(cfg: dict) -> dict:
    """Prompt user for any missing required fields using questionary.

    Mutates and returns the passed-in dict.
    """
    import questionary

    if not cfg.get("vm_name"):
        val = questionary.text("VM Name:").ask()
        if val is None:
            raise SystemExit("Cancelled.")
        cfg["vm_name"] = val.strip()

    if not cfg.get("hostname"):
        default = cfg["vm_name"].replace(" ", "-").lower()
        val = questionary.text("Hostname:", default=default).ask()
        if val is None:
            raise SystemExit("Cancelled.")
        cfg["hostname"] = (val or default).strip()

    if not cfg.get("ip_address"):
        val = questionary.text(
            "IP Address with CIDR (e.g. 192.168.1.10/24):",
            validate=lambda v: True if "/" in v else "CIDR required, e.g. 192.168.1.10/24",
        ).ask()
        if val is None:
            raise SystemExit("Cancelled.")
        cfg["ip_address"] = val.strip()

    if not cfg.get("gateway"):
        val = questionary.text("Gateway:").ask()
        if val is None:
            raise SystemExit("Cancelled.")
        cfg["gateway"] = val.strip()

    if not cfg.get("username"):
        val = questionary.text("Admin Username:", default="admin").ask()
        if val is None:
            raise SystemExit("Cancelled.")
        cfg["username"] = (val or "admin").strip()

    if not cfg.get("password"):
        val = questionary.password("Admin Password:").ask()
        if val is None or not val:
            raise SystemExit("Cancelled.")
        cfg["password"] = val

    if not cfg.get("hypervisor"):
        val = questionary.select(
            "Hypervisor:",
            choices=[
                questionary.Choice("VMware Workstation / Fusion", value="vmware"),
                questionary.Choice("Microsoft Hyper-V", value="hyperv"),
            ],
        ).ask()
        if val is None:
            raise SystemExit("Cancelled.")
        cfg["hypervisor"] = val

    return cfg


# ---------------------------------------------------------------------------
# NinjaOne helpers
# ---------------------------------------------------------------------------

def _resolve_ninjaone_installer(cfg: dict) -> str:
    """Authenticate with NinjaOne, resolve org/location IDs, and return installer URL."""
    client_id = cfg.get("ninjaone_client_id")
    client_secret = cfg.get("ninjaone_client_secret")
    region = cfg.get("ninjaone_region", "US")
    base_url = cfg.get("ninjaone_base_url")

    if not client_id or not client_secret:
        raise ValueError("NinjaOne client-id and client-secret are required.")

    console.print("[bold blue]Authenticating with NinjaOne…[/bold blue]")
    client = NinjaOneClient(client_id, client_secret, region, base_url=base_url)
    client.authenticate()

    orgs = client.list_organizations()
    if not orgs:
        raise NinjaOneAPIError(
            "No organizations accessible with the provided credentials."
        )

    org_id = cfg.get("ninjaone_org_id")
    org_name = cfg.get("ninjaone_org_name") or cfg.get("ninjaone_org")
    location_id = cfg.get("ninjaone_location_id")
    location_name = cfg.get("ninjaone_location_name") or cfg.get("ninjaone_location")

    # Resolve org
    if org_id is not None:
        org_id = int(org_id)
    elif org_name:
        match = client.get_org_by_name(org_name)
        if match:
            org_id, matched_name = match
            console.print(
                f"[dim]Matched organization: {matched_name} (ID {org_id})[/dim]"
            )
        else:
            raise ValueError(f"NinjaOne organization '{org_name}' not found.")
    else:
        if len(orgs) == 1:
            org_id, org_name = orgs[0]
            console.print(
                f"[dim]Auto-selected organization: {org_name} (ID {org_id})[/dim]"
            )
        else:
            raise ValueError(
                "Multiple NinjaOne organizations found. "
                "Please specify --ninjaone-org-id, --ninjaone-org-name, or --ninjaone-org."
            )

    # Resolve location
    locs = client.list_locations(org_id)
    if not locs:
        raise NinjaOneAPIError(f"No locations found for organization {org_id}.")

    if location_id is not None:
        location_id = int(location_id)
    elif location_name:
        match = client.get_location_by_name(org_id, location_name)
        if match:
            location_id, matched_name = match
            console.print(
                f"[dim]Matched location: {matched_name} (ID {location_id})[/dim]"
            )
        else:
            raise ValueError(f"NinjaOne location '{location_name}' not found.")
    else:
        if len(locs) == 1:
            location_id, location_name = locs[0]
            console.print(
                f"[dim]Auto-selected location: {location_name} (ID {location_id})[/dim]"
            )
        else:
            raise ValueError(
                "Multiple NinjaOne locations found. "
                "Please specify --ninjaone-location-id, --ninjaone-location-name, or --ninjaone-location."
            )

    installer_url = client.get_installer_url(
        org_id, location_id, installer_type="LINUX_DEB"
    )
    console.print("[green]Installer URL resolved.[/green]")
    return installer_url


# ---------------------------------------------------------------------------
# ISO creation
# ---------------------------------------------------------------------------

def create_config_iso(
    user_data_path: Path, meta_data_path: Path, output_path: Path
) -> None:
    """Create a cidata ISO from user-data and meta-data files."""
    for tool in ("mkisofs", "genisoimage", "oscdimg"):
        if shutil.which(tool):
            break
    else:
        console.print(
            Panel(
                "[yellow]No ISO-creation tool found (mkisofs, genisoimage, or oscdimg).\n"
                "Please create the config ISO manually.[/yellow]",
                title="ISO Creation Skipped",
                border_style="yellow",
            )
        )
        return

    cmd: list[str]
    if tool == "oscdimg":
        cmd = [
            tool,
            "-n",
            "-d",
            "-m",
            str(user_data_path.parent),
            str(output_path),
        ]
    else:
        cmd = [
            tool,
            "-output",
            str(output_path),
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            str(user_data_path.parent),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ISO creation failed ({tool}): {result.stderr or result.stdout}"
        )


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    global_cfg = _load_global_config()

    profile: dict | None = None
    if args.profile:
        profile = _load_profile(args.profile)

    non_interactive_flags = [
        args.hypervisor,
        args.vm_name,
        args.hostname,
        args.ip,
        args.gateway,
        args.username,
        args.password,
    ]
    interactive_needed = not any(non_interactive_flags)

    try:
        if interactive_needed:
            console.print(
                Panel.fit(
                    "[bold green]VM Deploy Tool[/bold green]",
                    border_style="green",
                )
            )
            vm_config = run_wizard(profile)
        else:
            vm_config = _build_config_from_args(args, profile, global_cfg)
            # Semi-interactive: prompt for any fields still missing after CLI + profile + global
            if vm_config.get("_missing_fields"):
                console.print(
                    f"[yellow]Missing {len(vm_config['_missing_fields'])} field(s); prompting…[/yellow]"
                )
                vm_config = _prompt_for_missing(vm_config)
                del vm_config["_missing_fields"]
        # Hash password if not already hashed
        if "password_hash" not in vm_config and vm_config.get("password"):
            vm_config["password_hash"] = hash_password(vm_config["password"])

        # ------------------------------------------------------------------
        # NinjaOne
        # ------------------------------------------------------------------
        installer_url = ""
        if (
            not args.dry_run
            and vm_config.get("ninjaone_client_id")
            and vm_config.get("ninjaone_client_secret")
        ):
            with console.status(
                "[bold green]Resolving NinjaOne installer…[/bold green]"
            ):
                installer_url = _resolve_ninjaone_installer(vm_config)
            if args.save_credentials:
                _save_ninjaone_credentials(
                    vm_config["ninjaone_client_id"],
                    vm_config["ninjaone_client_secret"],
                )
                console.print("[dim]NinjaOne credentials saved to OS keyring.[/dim]")
        elif args.dry_run and vm_config.get("ninjaone_client_id"):
            console.print(
                "[dim]Dry-run: skipping NinjaOne authentication.[/dim]"
            )
        else:
            if args.verbose:
                console.print(
                    "[dim]NinjaOne credentials not provided; skipping.[/dim]"
                )

        # ------------------------------------------------------------------
        # NetBird script
        # ------------------------------------------------------------------
        with console.status(
            "[bold green]Generating NetBird setup script…[/bold green]"
        ):
            if not vm_config.get("netbird_setup_key"):
                raise ValueError("NetBird setup key is required.")
            netbird_script = generate_setup_script(vm_config)
            vm_config["netbird_script"] = netbird_script

        # ------------------------------------------------------------------
        # NinjaOne script
        # ------------------------------------------------------------------
        with console.status(
            "[bold green]Generating NinjaOne setup script…[/bold green]"
        ):
            if installer_url:
                ninjaone_script = render_script(
                    "ninjaone-setup.sh.j2",
                    {"ninjaone_installer_url": installer_url},
                )
                vm_config["ninjaone_script"] = ninjaone_script
            else:
                vm_config["ninjaone_script"] = ""
                if args.verbose:
                    console.print(
                        "[dim]No NinjaOne installer URL; omitting setup script.[/dim]"
                    )

        # ------------------------------------------------------------------
        # Autoinstall YAML
        # ------------------------------------------------------------------
        with console.status(
            "[bold green]Rendering autoinstall YAML…[/bold green]"
        ):
            autoinstall_yaml = render_autoinstall(vm_config)

        if args.dry_run:
            console.print(
                Panel(
                    "[bold cyan]Dry Run — Autoinstall YAML[/bold cyan]",
                    border_style="cyan",
                )
            )
            console.print(autoinstall_yaml)
            return EXIT_SUCCESS

        # ------------------------------------------------------------------
        # Write temp files + ISO
        # ------------------------------------------------------------------
        tmpdir = Path(tempfile.mkdtemp(prefix="vm-deploy-"))
        user_data_path = tmpdir / "user-data"
        meta_data_path = tmpdir / "meta-data"
        iso_path = tmpdir / "cidata.iso"

        user_data_path.write_text(autoinstall_yaml, encoding="utf-8")
        meta_data_path.write_text(
            f"instance-id: {vm_config.get('hostname', 'vm-deploy')}\n"
            f"local-hostname: {vm_config.get('hostname', 'vm-deploy')}\n",
            encoding="utf-8",
        )

        with console.status("[bold green]Building config ISO…[/bold green]"):
            create_config_iso(user_data_path, meta_data_path, iso_path)

        # ------------------------------------------------------------------
        # Deploy
        # ------------------------------------------------------------------
        hypervisor = vm_config.get("hypervisor", "vmware")
        deployer = vmware_deployer if hypervisor == "vmware" else hyperv_deployer
        deployer_error = (
            vmware_deployer.VMwareDeployError
            if hypervisor == "vmware"
            else hyperv_deployer.HyperVDeployError
        )

        with console.status(
            f"[bold green]Deploying to {hypervisor.upper()}…[/bold green]"
        ):
            result = deployer.deploy(vm_config, str(iso_path))

        # ------------------------------------------------------------------
        # Result display
        # ------------------------------------------------------------------
        success = result.get("success", False)
        ip = result.get("ip") or "N/A"

        table = Table(title="Deployment Result", show_header=False)
        table.add_row("Hypervisor", hypervisor.upper())
        table.add_row("VM Name", str(vm_config.get("vm_name", "N/A")))
        table.add_row("Hostname", str(vm_config.get("hostname", "N/A")))
        table.add_row("IP Address", ip)
        table.add_row(
            "Status",
            "[green]Success[/green]" if success else "[red]Failed[/red]",
        )
        console.print(table)

        # Save profile if requested
        if args.save_profile:
            _save_profile(args.save_profile, vm_config)

        return EXIT_SUCCESS if success else EXIT_DEPLOY_ERROR

    except ValueError as exc:
        console.print(Panel(f"[red]{exc}[/red]", title="Validation Error", border_style="red"))
        return EXIT_VALIDATION_ERROR
    except NinjaOneAuthError as exc:
        console.print(Panel(f"[red]{exc}[/red]", title="Authentication Error", border_style="red"))
        return EXIT_AUTH_ERROR
    except (NinjaOneAPIError, deployer_error) as exc:
        console.print(Panel(f"[red]{exc}[/red]", title="Deployment Error", border_style="red"))
        return EXIT_DEPLOY_ERROR
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        return EXIT_DEPLOY_ERROR


if __name__ == "__main__":
    sys.exit(main())
