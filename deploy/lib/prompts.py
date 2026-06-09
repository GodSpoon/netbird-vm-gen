"""Interactive CLI wizard for VM deployment configuration."""

from __future__ import annotations

import re
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

INSTALLERS = {
    "netbird": {
        "name": "NetBird VPN",
        "template": "netbird-setup.sh.j2",
        "prompts": [
            {
                "key": "setup_key",
                "prompt": "NetBird Setup Key",
                "type": "password",
            },
            {
                "key": "management_url",
                "prompt": "Management URL",
                "default": "https://api.netbird.io",
            },
        ],
    },
    "ninjaone": {
        "name": "NinjaOne Agent",
        "template": "ninjaone-setup.sh.j2",
        "prompts": [
            {
                "key": "client_id",
                "prompt": "NinjaOne API Client ID",
                "type": "password",
            },
            {
                "key": "client_secret",
                "prompt": "NinjaOne API Client Secret",
                "type": "password",
            },
            {"key": "region", "prompt": "Region", "default": "US"},
            {"key": "organization", "prompt": "Organization"},
            {"key": "location", "prompt": "Location"},
        ],
    },
}

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_RE_IP_CIDR = re.compile(
    r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})$"
)


def _validate_ip_cidr(value: str) -> str | bool:
    if not value or not value.strip():
        return "IP Address with CIDR is required."
    match = _RE_IP_CIDR.match(value.strip())
    if not match:
        return "Invalid format. Expected e.g. 192.168.1.10/24"
    a, b, c, d, prefix = map(int, match.groups())
    for octet in (a, b, c, d):
        if octet > 255:
            return "Each octet must be 0-255."
    if not (0 <= prefix <= 32):
        return "CIDR prefix must be 0-32."
    return True


def _validate_required(value: str) -> str | bool:
    if not value or not value.strip():
        return "This field is required."
    return True


def _validate_ip(value: str) -> str | bool:
    if not value or not value.strip():
        return "This field is required."
    parts = value.strip().split(".")
    if len(parts) != 4:
        return "Invalid IPv4 address."
    for p in parts:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            return "Invalid IPv4 address."
    return True


def _qtext(label: str, default: str = "") -> questionary.Question:
    kwargs: dict[str, Any] = {"message": label}
    if default:
        kwargs["default"] = default
    return questionary.text(**kwargs)


def _qpassword(label: str) -> questionary.Question:
    return questionary.password(message=label)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

def run_wizard(profile: dict | None = None) -> dict:
    """Run the 8-step deployment wizard.

    Args:
        profile: Optional profile dict to pre-fill defaults.

    Returns:
        Flat-ish dict with all collected configuration values.
    """
    profile = profile or {}
    result: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 1. Select hypervisor
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 1 / 8[/bold cyan] — Select Hypervisor", expand=False))
    hypervisor = questionary.select(
        "Hypervisor:",
        choices=[
            questionary.Choice("VMware Workstation / Fusion", value="vmware"),
            questionary.Choice("Microsoft Hyper-V", value="hyper-v"),
        ],
        default=profile.get("hypervisor", "vmware"),
    ).ask()
    if hypervisor is None:
        raise SystemExit("Wizard cancelled.")
    result["hypervisor"] = hypervisor

    # ------------------------------------------------------------------
    # 2. VM Details
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 2 / 8[/bold cyan] — VM Details", expand=False))
    vm_name = _qtext("VM Name", profile.get("name", "")).ask()
    if vm_name is None:
        raise SystemExit("Wizard cancelled.")
    while not vm_name or not vm_name.strip():
        console.print("[red]VM Name is required.[/red]")
        vm_name = _qtext("VM Name").ask()
        if vm_name is None:
            raise SystemExit("Wizard cancelled.")
    result["name"] = vm_name.strip()

    hostname_default = profile.get("hostname", result["name"].replace(" ", "-").lower())
    hostname = _qtext("Hostname", hostname_default).ask()
    if hostname is None:
        raise SystemExit("Wizard cancelled.")
    result["hostname"] = (hostname or hostname_default).strip()

    description = _qtext("Description", profile.get("description", "")).ask()
    if description is None:
        raise SystemExit("Wizard cancelled.")
    result["description"] = (description or "").strip()

    # ------------------------------------------------------------------
    # 3. Hardware
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 3 / 8[/bold cyan] — Hardware", expand=False))
    vcpus_raw = _qtext("vCPUs", str(profile.get("vcpus", 2))).ask()
    if vcpus_raw is None:
        raise SystemExit("Wizard cancelled.")
    try:
        result["vcpus"] = int(vcpus_raw)
    except ValueError:
        result["vcpus"] = 2

    memory_raw = _qtext("Memory (MB)", str(profile.get("memory_mb", 2048))).ask()
    if memory_raw is None:
        raise SystemExit("Wizard cancelled.")
    try:
        result["memory_mb"] = int(memory_raw)
    except ValueError:
        result["memory_mb"] = 2048

    disk_raw = _qtext("Disk (GB)", str(profile.get("disk_gb", 25))).ask()
    if disk_raw is None:
        raise SystemExit("Wizard cancelled.")
    try:
        result["disk_gb"] = int(disk_raw)
    except ValueError:
        result["disk_gb"] = 25

    # ------------------------------------------------------------------
    # 4. Network
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 4 / 8[/bold cyan] — Network", expand=False))
    ip_cidr = questionary.text(
        "IP Address with CIDR (e.g. 192.168.1.10/24):",
        default=profile.get("ip_address", ""),
        validate=_validate_ip_cidr,
    ).ask()
    if ip_cidr is None:
        raise SystemExit("Wizard cancelled.")
    result["ip_address"] = ip_cidr.strip()

    gateway = questionary.text(
        "Gateway:",
        default=profile.get("gateway", ""),
        validate=_validate_ip,
    ).ask()
    if gateway is None:
        raise SystemExit("Wizard cancelled.")
    result["gateway"] = gateway.strip()

    dns_default = profile.get("dns_servers", "8.8.8.8, 8.8.4.4")
    if isinstance(dns_default, list):
        dns_default = ", ".join(str(d) for d in dns_default)
    dns_servers = _qtext("DNS Servers (comma-separated)", dns_default).ask()
    if dns_servers is None:
        raise SystemExit("Wizard cancelled.")
    result["dns_servers"] = [s.strip() for s in (dns_servers or "8.8.8.8").split(",") if s.strip()]

    search_domain = _qtext("Search Domain (optional)", profile.get("search_domain", "")).ask()
    if search_domain is None:
        raise SystemExit("Wizard cancelled.")
    sd = (search_domain or "").strip()
    if sd:
        result["search_domain"] = sd

    # ------------------------------------------------------------------
    # 5. Admin Credentials
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 5 / 8[/bold cyan] — Admin Credentials", expand=False))
    username = _qtext("Admin Username", profile.get("username", "admin")).ask()
    if username is None:
        raise SystemExit("Wizard cancelled.")
    while not username or not username.strip():
        console.print("[red]Username is required.[/red]")
        username = _qtext("Admin Username", "admin").ask()
        if username is None:
            raise SystemExit("Wizard cancelled.")
    result["username"] = username.strip()

    password = _qpassword("Admin Password").ask()
    if password is None:
        raise SystemExit("Wizard cancelled.")
    while not password:
        console.print("[red]Password is required.[/red]")
        password = _qpassword("Admin Password").ask()
        if password is None:
            raise SystemExit("Wizard cancelled.")
    result["password"] = password

    ssh_key = _qtext("SSH Public Key (optional)", profile.get("ssh_key", "")).ask()
    if ssh_key is None:
        raise SystemExit("Wizard cancelled.")
    sk = (ssh_key or "").strip()
    if sk:
        result["ssh_key"] = sk

    # ------------------------------------------------------------------
    # 6. NetBird
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 6 / 8[/bold cyan] — NetBird VPN", expand=False))
    nb_profile = profile.get("netbird", {})
    setup_key = _qpassword(
        "NetBird Setup Key",
    ).ask()
    if setup_key is None:
        raise SystemExit("Wizard cancelled.")
    while not setup_key:
        console.print("[red]Setup key is required.[/red]")
        setup_key = _qpassword("NetBird Setup Key").ask()
        if setup_key is None:
            raise SystemExit("Wizard cancelled.")
    result["netbird_setup_key"] = setup_key

    mgmt_url = _qtext(
        "Management URL",
        nb_profile.get("management_url", "https://api.netbird.io"),
    ).ask()
    if mgmt_url is None:
        raise SystemExit("Wizard cancelled.")
    result["netbird_management_url"] = (mgmt_url or "https://api.netbird.io").strip()

    # ------------------------------------------------------------------
    # 7. NinjaOne
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 7 / 8[/bold cyan] — NinjaOne Agent", expand=False))
    console.print("[dim]NinjaOne API Setup:[/dim]")
    console.print("  1. Go to Administration -> Apps -> API")
    console.print("  2. Click 'Add client app'")
    console.print("  3. Grant scopes: Monitoring, Management, Control, Refresh Token")
    console.print("  4. Copy the Client ID and Client Secret")
    console.print("  [link=https://4eos.rmmservices.net/#/administration/apps/api]https://4eos.rmmservices.net/#/administration/apps/api[/link]")
    console.print()

    nj_profile = profile.get("ninjaone", {}) if isinstance(profile, dict) else {}
    if not isinstance(nj_profile, dict):
        nj_profile = {}

    nj_region = _qtext("Region", nj_profile.get("region", "US")).ask()
    if nj_region is None:
        raise SystemExit("Wizard cancelled.")
    result["ninjaone_region"] = (nj_region or "US").strip()

    nj_base_url = _qtext(
        "Custom Base URL (optional, e.g. https://4eos.rmmservices.net)",
        nj_profile.get("base_url", ""),
    ).ask()
    if nj_base_url is None:
        raise SystemExit("Wizard cancelled.")
    if nj_base_url and nj_base_url.strip():
        result["ninjaone_base_url"] = nj_base_url.strip()

    nj_client_id = _qpassword("NinjaOne API Client ID").ask()
    if nj_client_id is None:
        raise SystemExit("Wizard cancelled.")
    while not nj_client_id:
        console.print("[red]Client ID is required.[/red]")
        nj_client_id = _qpassword("NinjaOne API Client ID").ask()
        if nj_client_id is None:
            raise SystemExit("Wizard cancelled.")
    result["ninjaone_client_id"] = nj_client_id

    nj_client_secret = _qpassword("NinjaOne API Client Secret").ask()
    if nj_client_secret is None:
        raise SystemExit("Wizard cancelled.")
    while not nj_client_secret:
        console.print("[red]Client Secret is required.[/red]")
        nj_client_secret = _qpassword("NinjaOne API Client Secret").ask()
        if nj_client_secret is None:
            raise SystemExit("Wizard cancelled.")
    result["ninjaone_client_secret"] = nj_client_secret

    nj_org = _qtext("Organization", nj_profile.get("organization", "")).ask()
    if nj_org is None:
        raise SystemExit("Wizard cancelled.")
    while not nj_org or not nj_org.strip():
        console.print("[red]Organization is required.[/red]")
        nj_org = _qtext("Organization").ask()
        if nj_org is None:
            raise SystemExit("Wizard cancelled.")
    result["ninjaone_org"] = nj_org.strip()

    nj_loc = _qtext("Location", nj_profile.get("location", "")).ask()
    if nj_loc is None:
        raise SystemExit("Wizard cancelled.")
    while not nj_loc or not nj_loc.strip():
        console.print("[red]Location is required.[/red]")
        nj_loc = _qtext("Location").ask()
        if nj_loc is None:
            raise SystemExit("Wizard cancelled.")
    result["ninjaone_location"] = nj_loc.strip()
    # ------------------------------------------------------------------
    # 8. Review and Deploy
    # ------------------------------------------------------------------
    console.print(Panel("[bold cyan]Step 8 / 8[/bold cyan] — Review and Deploy", expand=False))

    table = Table(title="Deployment Summary", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="dim", width=24)
    table.add_column("Value")

    table.add_row("Hypervisor", result["hypervisor"])
    table.add_row("VM Name", result["name"])
    table.add_row("Hostname", result["hostname"])
    table.add_row("Description", result.get("description", "") or "—")
    table.add_row("vCPUs", str(result["vcpus"]))
    table.add_row("Memory (MB)", str(result["memory_mb"]))
    table.add_row("Disk (GB)", str(result["disk_gb"]))
    table.add_row("IP Address", result["ip_address"])
    table.add_row("Gateway", result["gateway"])
    table.add_row("DNS Servers", ", ".join(result["dns_servers"]))
    table.add_row("Search Domain", result.get("search_domain", "—"))
    table.add_row("Admin Username", result["username"])
    pw_text = Text("•" * min(len(result["password"]), 16), style="green")
    table.add_row("Admin Password", pw_text)
    table.add_row(
        "SSH Public Key",
        "provided" if result.get("ssh_key") else "—",
    )
    table.add_row("NetBird Setup Key", "•" * 8)
    table.add_row("NetBird Mgmt URL", result["netbird_management_url"])
    table.add_row("NinjaOne Region", result.get("ninjaone_region", "—"))
    table.add_row("NinjaOne Base URL", result.get("ninjaone_base_url", "—"))
    table.add_row("NinjaOne Client ID", "•" * 8)
    table.add_row("NinjaOne Client Secret", "•" * 8)
    table.add_row("NinjaOne Organization", result.get("ninjaone_org", "—"))
    table.add_row("NinjaOne Location", result.get("ninjaone_location", "—"))

    console.print(table)

    confirm = questionary.confirm(
        "Proceed with deployment?",
        default=True,
    ).ask()
    if not confirm:
        raise SystemExit("Deployment cancelled by user.")

    return result
