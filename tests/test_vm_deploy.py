"""Unit tests for deploy.vm_deploy CLI logic."""

import sys
from pathlib import Path
from argparse import Namespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from deploy.vm_deploy import (
    _validate_ip_cidr,
    _validate_positive_int,
    _build_config_from_args,
    _generate_vm_name,
    EXIT_SUCCESS,
    EXIT_VALIDATION_ERROR,
)


def test_validate_ip_cidr_valid():
    """Valid IP/CIDR should not raise."""
    _validate_ip_cidr("192.168.1.10/24")
    _validate_ip_cidr("10.0.0.1/8")


def test_validate_ip_cidr_invalid():
    """Invalid IP/CIDR should raise ValueError."""
    try:
        _validate_ip_cidr("not-an-ip")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "CIDR" in str(exc)


def test_validate_ip_cidr_missing_prefix():
    """IP without CIDR should raise ValueError."""
    try:
        _validate_ip_cidr("192.168.1.10")
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "CIDR" in str(exc)


def test_validate_positive_int():
    """Positive ints should pass; zero/negative should fail."""
    _validate_positive_int(1, "CPU")
    _validate_positive_int(100, "RAM")
    try:
        _validate_positive_int(0, "CPU")
        assert False, "Expected ValueError"
    except ValueError:
        pass
    try:
        _validate_positive_int(-1, "RAM")
        assert False, "Expected ValueError"
    except ValueError:
        pass


def test_validate_positive_int_none():
    """None should be ignored (not validated)."""
    _validate_positive_int(None, "CPU")


def _make_namespace(**kwargs) -> Namespace:
    """Build a Namespace with all required fields and overrides."""
    defaults = {
        "vm_name": "",
        "hostname": "",
        "description": "",
        "cpu": None,
        "ram": None,
        "disk": None,
        "ip": "",
        "gateway": "",
        "dns": None,
        "username": "",
        "password": "",
        "ssh_key": None,
        "netbird_setup_key": None,
        "netbird_management_url": None,
        "ninjaone_region": None,
        "ninjaone_base_url": None,
        "ninjaone_client_id": None,
        "ninjaone_client_secret": None,
        "ninjaone_org_id": None,
        "ninjaone_location_id": None,
        "ninjaone_org_name": None,
        "ninjaone_location_name": None,
        "ninjaone_org": None,
        "ninjaone_location": None,
        "hypervisor": None,
        "save_credentials": False,
    }
    defaults.update(kwargs)
    return Namespace(**defaults)


def test_build_config_from_args_minimal():
    """Should build config from CLI args."""
    args = _make_namespace(
        vm_name="TEST-VM",
        hostname="test-vm",
        cpu=2,
        ram=4096,
        disk=50,
        ip="192.168.1.10/24",
        gateway="192.168.1.1",
        dns="8.8.8.8,1.1.1.1",
        username="admin",
        password="secret",
        netbird_setup_key="nb-key",
        hypervisor="vmware",
    )
    cfg = _build_config_from_args(args, None, None)
    assert cfg["vm_name"] == "TEST-VM"
    assert cfg["hostname"] == "test-vm"
    assert cfg["CPU"] == 2
    assert cfg["MemoryMB"] == 4096
    assert cfg["hypervisor"] == "vmware"


def test_build_config_missing_required():
    """Missing required fields should raise ValueError."""
    args = _make_namespace()
    try:
        _build_config_from_args(args, None, None)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Missing" in str(exc)


def test_build_config_from_profile():
    """Profile defaults should be overridden by CLI args."""
    profile = {
        "vm_name": "PROFILE-VM",
        "cpu": 4,
        "hypervisor": "hyperv",
    }
    args = _make_namespace(
        vm_name="CLI-VM",
        hostname="host",
        ip="10.0.0.1/24",
        gateway="10.0.0.254",
        username="admin",
        password="pass",
    )
    cfg = _build_config_from_args(args, profile, None)
    assert cfg["vm_name"] == "CLI-VM"  # CLI overrides profile
    assert cfg["cpu"] == 4  # Profile default kept
    assert cfg["hypervisor"] == "hyperv"  # Profile default kept


def test_generate_vm_name():
    """Should generate incremented VM names."""
    assert _generate_vm_name("Acme Corp") == "netbird-acme-corp-01"
    assert _generate_vm_name("Acme Corp", ["netbird-acme-corp-01"]) == "netbird-acme-corp-02"
    assert _generate_vm_name("Acme Corp", ["netbird-acme-corp-01", "netbird-acme-corp-03"]) == "netbird-acme-corp-02"


def test_generate_vm_name_special_chars():
    assert _generate_vm_name("Systems Engineering & Sales (SESCO)") == "netbird-systems-engineering-sales-sesco-01"


def test_global_config_layer():
    """Global config should provide defaults overridden by CLI args."""
    global_cfg = {"username": "globaladmin", "cpu": 8}
    args = _make_namespace(
        vm_name="TEST",
        hostname="test",
        ip="10.0.0.1/24",
        gateway="10.0.0.1",
        username="cliadmin",
        password="pass",
        hypervisor="vmware",
    )
    cfg = _build_config_from_args(args, None, global_cfg)
    assert cfg["username"] == "cliadmin"  # CLI overrides global
    assert cfg["cpu"] == 8  # Global default kept


def test_build_config_with_base_url():
    """Custom base URL should be passed through."""
    args = _make_namespace(
        vm_name="TEST",
        hostname="test",
        ip="10.0.0.1/24",
        gateway="10.0.0.1",
        username="admin",
        password="pass",
        hypervisor="vmware",
        ninjaone_base_url="https://4eos.rmmservices.net",
    )
    cfg = _build_config_from_args(args, None, None)
    assert cfg["ninjaone_base_url"] == "https://4eos.rmmservices.net"
