#!/usr/bin/env python3
"""Validation runner for vm-deploy-tool.

Runs all checks and reports failures. Designed for iterative bug-fix loops.
Usage:
    python validate.py          # Run all checks
    python validate.py --quick  # Skip slow checks
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd or PROJECT_ROOT
    )
    return result.returncode, result.stdout, result.stderr


def check(name: str, passed: bool, details: str = "") -> bool:
    """Print a check result and return the pass status."""
    status = "PASS" if passed else "FAIL"
    color = "\033[92m" if passed else "\033[91m"
    reset = "\033[0m"
    print(f"  {color}{status}{reset} — {name}")
    if details and not passed:
        for line in details.strip().splitlines():
            print(f"       {line}")
    return passed


def validate_python_syntax() -> bool:
    """Check all Python files compile."""
    print("\n[Python Syntax]")
    all_ok = True
    py_files = list(PROJECT_ROOT.rglob("*.py"))
    for f in py_files:
        if ".venv" in str(f):
            continue
        rc, _, err = run([sys.executable, "-m", "py_compile", str(f)])
        if not check(f"{f.relative_to(PROJECT_ROOT)}", rc == 0, err):
            all_ok = False
    return all_ok


def validate_imports() -> bool:
    """Check key modules import without error."""
    print("\n[Module Imports]")
    modules = [
        "deploy.lib.config_builder",
        "deploy.lib.ninjaone_client",
        "deploy.lib.netbird_installer",
        "deploy.lib.prompts",
        "deploy.lib.vmware_deployer",
        "deploy.lib.hyperv_deployer",
    ]
    all_ok = True
    for mod in modules:
        root = str(PROJECT_ROOT).replace("\\", "/")
        code = f"import sys; sys.path.insert(0, r'{root}'); __import__('{mod}')"
        rc, _, err = run([sys.executable, "-c", code])
        if not check(mod, rc == 0, err):
            all_ok = False
    return all_ok

def validate_pytest() -> bool:
    """Run pytest unit tests."""
    print("\n[Pytest Unit Tests]")
    rc, out, err = run([sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"])
    passed = rc == 0
    check("pytest suite", passed, out + err if not passed else "")
    return passed


def validate_templates_exist() -> bool:
    """Check all expected Jinja2 templates exist."""
    print("\n[Template Files]")
    templates_dir = PROJECT_ROOT / "deploy" / "templates"
    expected = ["autoinstall.yaml.j2", "netbird-setup.sh.j2", "ninjaone-setup.sh.j2"]
    all_ok = True
    for name in expected:
        exists = (templates_dir / name).exists()
        check(name, exists)
        if not exists:
            all_ok = False
    return all_ok


def validate_packer_files() -> bool:
    """Check Packer configs are present."""
    print("\n[Packer Files]")
    packer_dir = PROJECT_ROOT / "packer"
    expected = [
        "plugins.pkr.hcl",
        "variables.pkr.hcl",
        "build-vmware.pkr.hcl",
        "build-hyperv.pkr.hcl",
        "http/user-data",
        "http/meta-data",
        "scripts/provision.sh",
    ]
    all_ok = True
    for name in expected:
        exists = (packer_dir / name).exists()
        check(name, exists)
        if not exists:
            all_ok = False
    return all_ok


def validate_entry_point() -> bool:
    """Check vm_deploy.py --help works."""
    print("\n[CLI Entry Point]")
    rc, out, err = run([sys.executable, str(PROJECT_ROOT / "deploy" / "vm_deploy.py"), "--help"])
    passed = rc == 0 and "Deploy an Ubuntu VM" in out
    check("vm_deploy.py --help", passed, out + err if not passed else "")
    return passed


def validate_yaml_examples() -> bool:
    """Check example YAML files load."""
    print("\n[Example YAML]")
    import yaml

    all_ok = True
    examples_dir = PROJECT_ROOT / "examples"
    for f in examples_dir.glob("*.yaml"):
        try:
            with open(f) as fh:
                data = yaml.safe_load(fh)
            check(f.name, isinstance(data, dict))
            if not isinstance(data, dict):
                all_ok = False
        except Exception as exc:
            check(f.name, False, str(exc))
            all_ok = False
    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Validation runner for vm-deploy-tool")
    parser.add_argument("--quick", action="store_true", help="Skip slow checks")
    args = parser.parse_args()

    print("=" * 60)
    print("VM-Deploy-Tool Validation Suite")
    print("=" * 60)

    results = []
    results.append(("Python Syntax", validate_python_syntax()))
    results.append(("Module Imports", validate_imports()))
    results.append(("Template Files", validate_templates_exist()))
    results.append(("Packer Files", validate_packer_files()))
    results.append(("CLI Entry Point", validate_entry_point()))
    results.append(("Example YAML", validate_yaml_examples()))

    if not args.quick:
        results.append(("Pytest", validate_pytest()))

    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    print(f"Results: {passed}/{total} checks passed")

    if passed < total:
        print("\nFailed checks:")
        for name, ok in results:
            if not ok:
                print(f"  - {name}")
        print("\n\033[91mVALIDATION FAILED\033[0m")
        return 1

    print("\n\033[92mVALIDATION PASSED\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
