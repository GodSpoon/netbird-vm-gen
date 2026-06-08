"""Unit tests for deploy.lib.config_builder."""

import base64
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from deploy.lib.config_builder import hash_password, render_autoinstall, render_script

def test_hash_password_returns_sha512_prefix():
    """SHA-512 crypt hashes start with $6$."""
    hashed = hash_password("testpassword")
    assert hashed.startswith("$6$"), f"Expected $6$ prefix, got: {hashed[:10]}"


def test_hash_password_is_deterministic_in_format():
    """Two hashes of the same password should both be valid SHA-512 hashes."""
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1.startswith("$6$")
    assert h2.startswith("$6$")
    # Hashes should differ because of random salt
    assert h1 != h2


def test_render_script_netbird():
    """NetBird template renders with provided context."""
    result = render_script(
        "netbird-setup.sh.j2",
        {
            "netbird_setup_key": "nb-key-123",
            "netbird_management_url": "https://api.netbird.io",
            "hostname": "test-host",
        },
    )
    assert "nb-key-123" in result
    assert "test-host" in result
    assert "netbird up" in result


def test_render_script_ninjaone():
    """NinjaOne template renders with provided context."""
    result = render_script(
        "ninjaone-setup.sh.j2",
        {"ninjaone_installer_url": "https://example.com/agent.deb"},
    )
    assert "https://example.com/agent.deb" in result
    assert "dpkg -i" in result


def test_render_autoinstall_minimal():
    """Autoinstall renders with minimal required config."""
    vm_config = {
        "ip_address": "192.168.1.10/24",
        "gateway": "192.168.1.1",
        "dns_servers": "8.8.8.8,1.1.1.1",
        "hostname": "test-vm",
        "username": "admin",
        "password": "testpass",
        "ssh_key": "",
        "netbird_script": "#!/bin/bash\necho netbird",
        "ninjaone_script": "#!/bin/bash\necho ninjaone",
    }
    result = render_autoinstall(vm_config)
    assert "#cloud-config" in result
    assert "192.168.1.10" in result
    assert "test-vm" in result
    assert "admin" in result
    assert base64.b64encode(b"#!/bin/bash\necho netbird").decode("ascii") in result
    assert base64.b64encode(b"#!/bin/bash\necho ninjaone").decode("ascii") in result


def test_render_autoinstall_with_ssh_key():
    """Autoinstall includes SSH authorized keys when provided."""
    vm_config = {
        "ip_address": "10.0.0.5/24",
        "gateway": "10.0.0.1",
        "dns_servers": ["1.1.1.1"],
        "hostname": "ssh-vm",
        "username": "sysadmin",
        "password": "secret",
        "ssh_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDIhz2GK/XCUj4i6Q5yQJNL1MXMY0RxzPV2QrBqfHrDq user@host",
        "netbird_script": "",
        "ninjaone_script": "",
    }
    result = render_autoinstall(vm_config)
    assert "ssh-ed25519" in result
    assert "sysadmin" in result


def test_render_autoinstall_dhcp_mode():
    """When ip_address is empty, network should still render."""
    vm_config = {
        "ip_address": "",
        "gateway": "",
        "dns_servers": [],
        "hostname": "dhcp-vm",
        "username": "admin",
        "password": "pass",
        "ssh_key": "",
        "netbird_script": "",
        "ninjaone_script": "",
    }
    result = render_autoinstall(vm_config)
    assert "dhcp-vm" in result
    assert "#cloud-config" in result
