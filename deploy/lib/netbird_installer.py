"""NetBird installer setup script generator.

Thin façade over the template renderer that produces the cloud-init/netbird
setup script for a VM.
"""

from pathlib import Path

from .config_builder import render_script


def generate_setup_script(config: dict) -> str:
    """Render the NetBird setup script from *config*.

    Parameters
    ----------
    config: dict
        Must contain at least ``netbird_setup_key`` and ``hostname``.
        May optionally contain ``netbird_management_url`` (defaults to
        https://api.netbird.io).

    Returns
    -------
    str
        Rendered shell script content.
    """
    context = {
        "netbird_setup_key": config["netbird_setup_key"],
        "netbird_management_url": config.get(
            "netbird_management_url", "https://api.netbird.io"
        ),
        "hostname": config["hostname"],
    }
    return render_script("netbird-setup.sh.j2", context)
