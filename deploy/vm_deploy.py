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

import sys
from pathlib import Path

# Allow imports when script is run directly: python deploy/vm_deploy.py
_deploy_dir = Path(__file__).parent
_project_root = _deploy_dir.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import argparse
import ipaddress
import os
import shutil
import subprocess
import tempfile
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.status import Status

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
# CLI definition
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vm-deploy",
        description="Deploy an Ubuntu VM with NetBird and NinjaOne pre-installed.",
    )
    parser.add_argument("--profile", type=Path, help="YAML client profile to pre-fill defaults.")
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
    parser.add_argument("--ip", help="Static IP with CIDR, e.g. 192.168.1.10/24.")
    parser.add_argument("--gateway", help="Default gateway.")
    parser.add_argument("--dns", help="DNS server(s), comma-separated.")
    parser.add_argument("--username", help="Admin username.")
    parser.add_argument("--password", help="Admin password (will be hashed).")
    parser.add_argument("--ssh-key", type=Path, help="Path to SSH public-key file (optional).")
    parser.add_argument("--netbird-setup-key", help="NetBird setup key.")
    parser.add_argument(
        "--netbird-management-url",
        default="https://api.netbird.io",
        help="NetBird management URL (default: https://api.netbird.io).",
    )
    parser.add_argument("--ninjaone-region", default="US", help="NinjaOne region (default: US).")
    parser.add_argument("--ninjaone-client-id", help="NinjaOne API client ID.")
    parser.add_argument("--ninjaone-client-secret", help="NinjaOne API client secret.")
    parser.add_argument("--ninjaone-org", help="NinjaOne organization name or ID.")
    parser.add_argument("--ninjaone-location", help="NinjaOne location name or ID.")
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
        raise ValueError(f"IP must include CIDR prefix (e.g. 192.168.1.10/24): got '{value}'")
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

def _load_profile(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"Profile file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("Profile YAML must be a mapping.")
    return data


# ---------------------------------------------------------------------------
# Non-interactive config builder
# ---------------------------------------------------------------------------

def _build_config_from_args(args: argparse.Namespace, profile: dict | None) -> dict:
    """Merge CLI args on top of profile defaults, validating as we go."""
    cfg: dict[str, Any] = dict(profile) if profile else {}

    # Direct mappings
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
        ("ninjaone_client_id", args.ninjaone_client_id),
        ("ninjaone_client_secret", args.ninjaone_client_secret),
        ("ninjaone_org", args.ninjaone_org),
        ("ninjaone_location", args.ninjaone_location),
    ):
        if arg_val is not None:
            cfg[key] = arg_val

    # DNS handling
    if args.dns is not None:
        cfg["dns_servers"] = [s.strip() for s in args.dns.split(",") if s.strip()]

    # SSH key handling
    if args.ssh_key is not None:
        if not args.ssh_key.exists():
            raise ValueError(f"SSH key file not found: {args.ssh_key}")
        cfg["ssh_key"] = args.ssh_key.read_text(encoding="utf-8").strip()

    # Validation
    required = ["vm_name", "hostname", "ip_address", "gateway", "username", "password"]
    missing = [f for f in required if not cfg.get(f)]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    _validate_ip_cidr(cfg["ip_address"])
    _validate_positive_int(cfg.get("cpu"), "CPU")
    _validate_positive_int(cfg.get("ram"), "RAM")
    _validate_positive_int(cfg.get("disk"), "Disk")

    # Hypervisor
    if args.hypervisor:
        cfg["hypervisor"] = args.hypervisor
    elif not cfg.get("hypervisor"):
        raise ValueError("--hypervisor is required in non-interactive mode.")

    # Normalise keys for downstream deployers
    if "cpu" in cfg and "CPU" not in cfg:
        cfg["CPU"] = cfg["cpu"]
    if "ram" in cfg and "MemoryMB" not in cfg:
        cfg["MemoryMB"] = cfg["ram"]
    if "disk" in cfg and "DiskGB" not in cfg:
        cfg["DiskGB"] = cfg["disk"]

    return cfg


# ---------------------------------------------------------------------------
# NinjaOne helpers
# ---------------------------------------------------------------------------

def _resolve_ninjaone_installer(cfg: dict) -> str:
    """Authenticate with NinjaOne, resolve org/location IDs, and return installer URL."""
    client_id = cfg.get("ninjaone_client_id")
    client_secret = cfg.get("ninjaone_client_secret")
    region = cfg.get("ninjaone_region", "US")

    if not client_id or not client_secret:
        raise ValueError("NinjaOne client-id and client-secret are required.")

    console.print("[bold blue]Authenticating with NinjaOne…[/bold blue]")
    client = NinjaOneClient(client_id, client_secret, region)
    client.authenticate()

    orgs = client.get_organizations()
    if not orgs:
        raise NinjaOneAPIError("No organizations accessible with the provided credentials.")

    org_id = cfg.get("ninjaone_org")
    location_id = cfg.get("ninjaone_location")

    # Resolve org name → id
    if org_id is not None and not str(org_id).isdigit():
        org_name = str(org_id)
        matches = [o for o in orgs if o.get("name") == org_name]
        if not matches:
            raise ValueError(f"NinjaOne organization '{org_name}' not found.")
        org_id = matches[0]["id"]
    else:
        if org_id is None:
            if len(orgs) == 1:
                org_id = orgs[0]["id"]
                console.print(f"[dim]Auto-selected organization: {orgs[0].get('name')} (ID {org_id})[/dim]")
            else:
                raise ValueError(
                    "Multiple NinjaOne organizations found. Please specify --ninjaone-org."
                )
        org_id = int(org_id)

    # Resolve location name → id
    locations = client.get_locations(org_id)
    if not locations:
        raise NinjaOneAPIError(f"No locations found for organization {org_id}.")

    if location_id is not None and not str(location_id).isdigit():
        loc_name = str(location_id)
        matches = [l for l in locations if l.get("name") == loc_name]
        if not matches:
            raise ValueError(f"NinjaOne location '{loc_name}' not found.")
        location_id = matches[0]["id"]
    else:
        if location_id is None:
            if len(locations) == 1:
                location_id = locations[0]["id"]
                console.print(
                    f"[dim]Auto-selected location: {locations[0].get('name')} (ID {location_id})[/dim]"
                )
            else:
                raise ValueError(
                    "Multiple NinjaOne locations found. Please specify --ninjaone-location."
                )
        location_id = int(location_id)

    installer_url = client.get_installer_url(org_id, location_id, installer_type="LINUX_DEB")
    console.print(f"[green]Installer URL resolved.[/green]")
    return installer_url


# ---------------------------------------------------------------------------
# ISO creation
# ---------------------------------------------------------------------------

def create_config_iso(user_data_path: Path, meta_data_path: Path, output_path: Path) -> None:
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
        raise RuntimeError(f"ISO creation failed ({tool}): {result.stderr or result.stdout}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    profile: dict | None = None
    if args.profile:
        profile = _load_profile(args.profile)

    # Determine whether we are in interactive mode.
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
            console.print(Panel.fit("[bold green]VM Deploy Tool[/bold green]", border_style="green"))
            vm_config = run_wizard(profile)
        else:
            vm_config = _build_config_from_args(args, profile)

        # Ensure IP is split (render_autoinstall also does this, but we keep the
        # canonical keys here for consistency).
        ip_cidr = vm_config.get("ip_address", "")
        if "/" in ip_cidr:
            ip_address, prefix = ip_cidr.split("/", 1)
            vm_config["ip_address"] = ip_address
            vm_config["cidr_prefix"] = prefix

        # Hash password if not already hashed
        if "password_hash" not in vm_config and vm_config.get("password"):
            vm_config["password_hash"] = hash_password(vm_config["password"])

        # ------------------------------------------------------------------
        # NinjaOne
        # ------------------------------------------------------------------
        installer_url = ""
        if vm_config.get("ninjaone_client_id") and vm_config.get("ninjaone_client_secret"):
            with console.status("[bold green]Resolving NinjaOne installer…[/bold green]"):
                installer_url = _resolve_ninjaone_installer(vm_config)
        else:
            if args.verbose:
                console.print("[dim]NinjaOne credentials not provided; skipping.[/dim]")

        # ------------------------------------------------------------------
        # NetBird script
        # ------------------------------------------------------------------
        with console.status("[bold green]Generating NetBird setup script…[/bold green]"):
            if not vm_config.get("netbird_setup_key"):
                raise ValueError("NetBird setup key is required.")
            netbird_script = generate_setup_script(vm_config)
            vm_config["netbird_script"] = netbird_script

        # ------------------------------------------------------------------
        # NinjaOne script
        # ------------------------------------------------------------------
        with console.status("[bold green]Generating NinjaOne setup script…[/bold green]"):
            if installer_url:
                ninjaone_script = render_script(
                    "ninjaone-setup.sh.j2",
                    {"ninjaone_installer_url": installer_url},
                )
                vm_config["ninjaone_script"] = ninjaone_script
            else:
                vm_config["ninjaone_script"] = ""
                if args.verbose:
                    console.print("[dim]No NinjaOne installer URL; omitting setup script.[/dim]")

        # ------------------------------------------------------------------
        # Autoinstall YAML
        # ------------------------------------------------------------------
        with console.status("[bold green]Rendering autoinstall YAML…[/bold green]"):
            autoinstall_yaml = render_autoinstall(vm_config)

        if args.dry_run:
            console.print(Panel("[bold cyan]Dry Run — Autoinstall YAML[/bold cyan]", border_style="cyan"))
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

        with console.status(f"[bold green]Deploying to {hypervisor.upper()}…[/bold green]"):
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
        table.add_row("Status", "[green]Success[/green]" if success else "[red]Failed[/red]")

        if not success:
            console.print(table)
            console.print(
                Panel(
                    f"[red]{result.get('error', 'Unknown deployment error')}[/red]",
                    title="Error Details",
                    border_style="red",
                )
            )
            return EXIT_DEPLOY_ERROR

        console.print(table)
        return EXIT_SUCCESS

    except (NinjaOneAuthError,) as exc:
        console.print(
            Panel(
                f"[red]NinjaOne authentication failed:[/red]\n{exc}",
                title="Authentication Error",
                border_style="red",
            )
        )
        return EXIT_AUTH_ERROR

    except (NinjaOneAPIError,) as exc:
        console.print(
            Panel(
                f"[red]NinjaOne API error:[/red]\n{exc}",
                title="API Error",
                border_style="red",
            )
        )
        return EXIT_DEPLOY_ERROR

    except (vmware_deployer.VMwareDeployError, hyperv_deployer.HyperVDeployError) as exc:
        console.print(
            Panel(
                f"[red]Deployment failed:[/red]\n{exc}",
                title="Deployment Error",
                border_style="red",
            )
        )
        return EXIT_DEPLOY_ERROR

    except ValueError as exc:
        console.print(
            Panel(
                f"[yellow]{exc}[/yellow]",
                title="Validation Error",
                border_style="yellow",
            )
        )
        return EXIT_VALIDATION_ERROR

    except Exception as exc:
        console.print(
            Panel(
                f"[red]Unexpected error:[/red]\n{exc}",
                title="Error",
                border_style="red",
            )
        )
        return EXIT_DEPLOY_ERROR


if __name__ == "__main__":
    sys.exit(main())
