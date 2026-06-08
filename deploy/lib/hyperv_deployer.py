"""Hyper-V deployer wrapper.

Executes ``scripts/deploy-hyperv.ps1`` via PowerShell and parses the result.
"""

import subprocess
import sys
import os
from pathlib import Path


SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "deploy-hyperv.ps1"


class HyperVDeployError(Exception):
    """Raised when the Hyper-V deployment fails."""
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


def _hyperv_enabled() -> bool:
    """Return True if the Microsoft-Hyper-V Windows feature is enabled."""
    result = subprocess.run(
        [
            "powershell",
            "-Command",
            'Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V | Select-Object -ExpandProperty State',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and "Enabled" in result.stdout


def _vm_switch_exists(switch_name: str) -> bool:
    """Return True if the named Hyper-V virtual switch exists."""
    result = subprocess.run(
        [
            "powershell",
            "-Command",
            f'Get-VMSwitch -Name "{switch_name}" -ErrorAction SilentlyContinue | Select-Object -First 1',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _parse_ip_from_stdout(stdout: str) -> str | None:
    """Attempt to extract an IPv4 address from deployment stdout."""
    import re
    for line in stdout.splitlines():
        line = line.strip()
        match = re.search(r"IP:\s*(\d{1,3}(?:\.\d{1,3}){3})", line)
        if match:
            return match.group(1)
        match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
        if match:
            ip = match.group(1)
            if not ip.startswith("0.0.0") and not ip.startswith("255."):
                return ip
    return None


def deploy(vm_config: dict, config_iso_path: str = "") -> dict:
    """Deploy a VM on Hyper-V.

    Parameters
    ----------
    vm_config: dict
        Dictionary with keys such as ``VMName``, ``TemplateVhdx``,
        ``VMSwitch``, ``VMPath``, ``CPU``, ``MemoryBytes``.
    config_iso_path: str, optional
        Path to the cloud-init configuration ISO to attach.

    Returns
    -------
    dict
        ``{'success': bool, 'stdout': str, 'stderr': str, 'ip': str|None}``

    Raises
    ------
    HyperVDeployError
        If a pre-requisite is missing or the PowerShell script fails.
    """
    if not _powershell_available():
        raise HyperVDeployError("PowerShell (pwsh or powershell) is not available on PATH.")

    if not _hyperv_enabled():
        raise HyperVDeployError("Hyper-V role (Microsoft-Hyper-V) is not enabled.")

    switch_name = vm_config.get("VMSwitch", "Default Switch")
    if not _vm_switch_exists(switch_name):
        raise HyperVDeployError(
            f"Hyper-V virtual switch '{switch_name}' does not exist."
        )

    args = [
        "pwsh",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT_PATH),
        "-VMName",
        vm_config.get("VMName", vm_config.get("hostname", "netbird-vm")),
    ]

    if "TemplateVhdx" in vm_config:
        args.extend(["-TemplateVhdx", str(vm_config["TemplateVhdx"])])
    if "VMSwitch" in vm_config:
        args.extend(["-VMSwitch", str(vm_config["VMSwitch"])])
    if "VMPath" in vm_config:
        args.extend(["-VMPath", str(vm_config["VMPath"])])
    if "CPU" in vm_config:
        args.extend(["-CPU", str(vm_config["CPU"])])
    if "MemoryBytes" in vm_config:
        args.extend(["-MemoryBytes", str(vm_config["MemoryBytes"])])
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
        raise HyperVDeployError(
            f"Hyper-V deployment failed (exit {result.returncode}): {result.stderr or result.stdout}"
        )

    return response
