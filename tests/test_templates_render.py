"""Integration tests: ensure all Jinja2 templates render without error."""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from deploy.lib.config_builder import render_script
from deploy.lib.netbird_installer import generate_setup_script

def test_all_templates_exist():
    """All expected templates should be present."""
    templates_dir = Path(__file__).parent.parent / "deploy" / "templates"
    expected = [
        "autoinstall.yaml.j2",
        "netbird-setup.sh.j2",
        "ninjaone-setup.sh.j2",
    ]
    for name in expected:
        assert (templates_dir / name).exists(), f"Missing template: {name}"


def test_autoinstall_template_render():
    """Full autoinstall should render with realistic config."""
    from deploy.lib.config_builder import render_autoinstall

    vm_config = {
        "ip_address": "172.16.5.20/24",
        "gateway": "172.16.5.1",
        "dns_servers": ["8.8.8.8", "1.1.1.1"],
        "hostname": "prod-web01",
        "username": "sysadmin",
        "password": "ComplexP@ssw0rd!",
        "ssh_key": "ssh-rsa AAAA... user@machine",
        "netbird_script": generate_setup_script({
            "netbird_setup_key": "nb-prod-key",
            "hostname": "prod-web01",
        }),
        "ninjaone_script": render_script(
            "ninjaone-setup.sh.j2",
            {"ninjaone_installer_url": "https://ninja.example.com/agent.deb"},
        ),
        "search_domain": "example.com",
    }
    result = render_autoinstall(vm_config)
    assert "#cloud-config" in result
    assert "autoinstall:" in result
    assert "172.16.5.20" in result
    assert "prod-web01" in result
    assert "sysadmin" in result
    assert "ssh-rsa" in result
    assert "example.com" in result
    # Verify scripts are embedded as base64 in late-commands
    assert "base64 -d" in result
    # Verify YAML is parseable
    import yaml
    data = yaml.safe_load(result)
    assert "late-commands" in data["autoinstall"]
    assert len(data["autoinstall"]["late-commands"]) >= 3


def test_netbird_script_is_executable_shell():
    """NetBird script should start with shebang."""
    script = generate_setup_script({
        "netbird_setup_key": "key",
        "hostname": "host",
    })
    assert script.strip().startswith("#!/usr/bin/env bash") or script.strip().startswith("#!/bin/bash")


def test_ninjaone_script_is_executable_shell():
    """NinjaOne script should start with shebang."""
    script = render_script(
        "ninjaone-setup.sh.j2",
        {"ninjaone_installer_url": "https://example.com/agent.deb"},
    )
    assert script.strip().startswith("#!/usr/bin/env bash") or script.strip().startswith("#!/bin/bash")
