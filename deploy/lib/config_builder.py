"""Jinja2-based configuration builder for VM deployment."""

from __future__ import annotations

import crypt
import os
import secrets
from pathlib import Path

import jinja2

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    trim_blocks=True,
    lstrip_blocks=True,
)


def hash_password(plain: str) -> str:
    """Hash a plaintext password using SHA-512 crypt.

    Args:
        plain: The plaintext password.

    Returns:
        The hashed password string suitable for /etc/shadow or cloud-init.
    """
    salt = crypt.mksalt(crypt.METHOD_SHA512)
    return crypt.crypt(plain, salt)


def render_autoinstall(vm_config: dict) -> str:
    """Render the autoinstall.yaml Jinja2 template.

    Args:
        vm_config: Flat-ish dict produced by the wizard containing keys such as
            ``ip_address``, ``gateway``, ``dns_servers``, ``hostname``,
            ``username``, ``password``, ``ssh_key``, ``netbird_script``,
            ``ninjaone_script``, and optionally ``search_domain``.

    Returns:
        Rendered autoinstall YAML string.
    """
    template = env.get_template("autoinstall.yaml.j2")

    # Derive network details from the IP/CIDR string (e.g. "192.168.1.10/24")
    ip_cidr = vm_config.get("ip_address", "")
    ip_address = ip_cidr
    cidr_prefix = ""
    if "/" in ip_cidr:
        ip_address, prefix = ip_cidr.split("/", 1)
        cidr_prefix = prefix

    dns_servers = vm_config.get("dns_servers", [])
    if isinstance(dns_servers, str):
        dns_servers = [s.strip() for s in dns_servers.split(",") if s.strip()]

    ssh_key_raw = vm_config.get("ssh_key", "")
    ssh_authorized_keys = [k.strip() for k in ssh_key_raw.splitlines() if k.strip()]

    context = {
        "network": {
            "dhcp": False,
            "ip_address": ip_address,
            "cidr_prefix": cidr_prefix,
            "gateway": vm_config.get("gateway", ""),
            "dns_servers": dns_servers,
            "search_domain": vm_config.get("search_domain", ""),
        },
        "hostname": vm_config.get("hostname", "ubuntu-vm"),
        "admin": {
            "username": vm_config.get("username", "admin"),
            "password_hash": hash_password(vm_config.get("password", "")),
            "allow_password": True,
            "ssh_authorized_keys": ssh_authorized_keys,
        },
        "netbird_script": vm_config.get("netbird_script", ""),
        "ninjaone_script": vm_config.get("ninjaone_script", ""),
    }

    return template.render(context)


def render_script(template_name: str, context: dict) -> str:
    """Render an arbitrary Jinja2 template.

    Args:
        template_name: Name of the template file (e.g. ``netbird-setup.sh.j2``).
        context: Dictionary of variables to inject into the template.

    Returns:
        Rendered template string.
    """
    template = env.get_template(template_name)
    return template.render(context)
