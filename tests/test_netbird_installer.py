"""Unit tests for deploy.lib.netbird_installer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from deploy.lib.netbird_installer import generate_setup_script


def test_generate_setup_script():
    """Should render NetBird setup script with all required values."""
    config = {
        "netbird_setup_key": "nb-skey-test123",
        "netbird_management_url": "https://custom.netbird.io",
        "hostname": "test-host",
    }
    script = generate_setup_script(config)
    assert "nb-skey-test123" in script
    assert "https://custom.netbird.io" in script
    assert "test-host" in script
    assert "netbird up" in script
    assert "set -euo pipefail" in script


def test_generate_setup_script_default_url():
    """Should use default management URL when not provided."""
    config = {
        "netbird_setup_key": "nb-key",
        "hostname": "host",
    }
    script = generate_setup_script(config)
    assert "https://api.netbird.io" in script
