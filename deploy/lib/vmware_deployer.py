"""VMware vSphere deployer wrapper.

Executes ``scripts/deploy-vmware.ps1`` via PowerShell and parses the result.
"""

import subprocess
import sys
import os
from pathlib import Path


SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "deploy-vmware.ps1"


class VMwareDeployError(Exception):
    """Raised when the VMware deployment fails."""
    pass


def _powershell_available() -> bool:
    """Return True if *pwsh* or *powershell* is on PATH."""
    for cmd in ("pwsh", "powershell"):
        result = subprocess.run(
            ["where", cmd],
            capture_output=True,
            text=True,
            shell=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    return False


def _powercli_installed() -> bool:
    """Return True if the VMware.PowerCLI module is available."""
    result = subprocess.run(
        ["pwsh", "-Command", "Get-Module -ListAvailable VMware.PowerCLI"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and "VMware.PowerCLI" in result.stdout


def _parse_ip_from_stdout(stdout: str) -> str | None:
    """Attempt to extract an IPv4 address from deployment stdout."""
    import re
    # Look for common patterns like "IP: 192.168.1.50" or "192.168.1.50"
    for line in stdout.splitlines():
        line = line.strip()
        match = re.search(r"IP:\s*(\d{1,3}(?:\.\d{1,3}){3})", line)
        if match:
            return match.group(1)
        match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
        if match:
            ip = match.group(1)
            # Exclude common false positives
            if not ip.startswith("0.0.0") and not ip.startswith("255."):
                return ip
    return None


def deploy(vm_config: dict, config_iso_path: str = "") -> dict:
    """Deploy a VM on VMware vSphere.

    Parameters
    ----------
    vm_config: dict
        Dictionary with keys such as ``VMName``, ``TemplateName``,
        ``VMHost``, ``Datastore``, ``CPU``, ``MemoryMB``, ``NetworkName``.
    config_iso_path: str, optional
        Path to the cloud-init configuration ISO to attach.

    Returns
    -------
    dict
        ``{'success': bool, 'stdout': str, 'stderr': str, 'ip': str|None}``

    Raises
    ------
    VMwareDeployError
        If a pre-requisite is missing or the PowerShell script fails.
    """
    if not _powershell_available():
        raise VMwareDeployError("PowerShell (pwsh or powershell) is not available on PATH.")

    if not _powercli_installed():
        raise VMwareDeployError("VMware.PowerCLI module is not installed.")

    args = [
        "pwsh",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT_PATH),
        "-VMName",
        vm_config.get("VMName", vm_config.get("hostname", "netbird-vm")),
        "-TemplateName",
        vm_config.get("TemplateName", "tpl-ubuntu-2404-base"),
    ]

    if "VMHost" in vm_config:
        args.extend(["-VMHost", str(vm_config["VMHost"])])
    if "Datastore" in vm_config:
        args.extend(["-Datastore", str(vm_config["Datastore"])])
    if "CPU" in vm_config:
        args.extend(["-CPU", str(vm_config["CPU"])])
    if "MemoryMB" in vm_config:
        args.extend(["-MemoryMB", str(vm_config["MemoryMB"])])
    if "NetworkName" in vm_config:
        args.extend(["-NetworkName", str(vm_config["NetworkName"])])
    if config_iso_path:
        args.extend(["-ConfigIsoPath", str(config_iso_path)])

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )

    ip = _parse_ip_from_stdout(result.stdout)

    response = {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "ip": ip,
    }

    if not response["success"]:
        raise VMwareDeployError(
            f"VMware deployment failed (exit {result.returncode}): {result.stderr or result.stdout}"
        )

    return response
