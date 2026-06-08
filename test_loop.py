#!/usr/bin/env python3
"""Iterative test runner — run this on every code change.

Usage:
    python test_loop.py           # Run once
    python test_loop.py --watch   # Re-run on file changes (requires watchdog)
    python test_loop.py --fuzz    # Run with randomized inputs
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description="Iterative test runner")
    parser.add_argument("--watch", action="store_true", help="Watch for changes and re-run")
    args = parser.parse_args()

    if args.watch:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            print("watchdog not installed. Run: pip install watchdog")
            return 1

        class Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.src_path.endswith(".py"):
                    print(f"\n[change detected: {event.src_path}]")
                    run_suite()

        observer = Observer()
        observer.schedule(Handler(), str(Path(__file__).parent), recursive=True)
        observer.start()
        print("Watching for changes... (Ctrl+C to stop)")
        run_suite()
        try:
            while True:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
        return 0
    else:
        return run_suite()


def run_suite() -> int:
    print("=" * 60)
    print("ITERATIVE TEST RUN")
    print("=" * 60)

    checks = [
        ([sys.executable, "-m", "py_compile", "deploy/vm_deploy.py"], "Syntax: vm_deploy.py"),
        ([sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "--color=yes"], "Unit tests"),
        ([sys.executable, "validate.py"], "Full validation"),
    ]

    all_ok = True
    for cmd, label in checks:
        print(f"\n>>> {label}")
        rc, out = run(cmd)
        if rc != 0:
            all_ok = False
            print(out)
            print(f"\n[FAIL] {label}")
        else:
            print(f"[PASS] {label}")

    print("\n" + "=" * 60)
    if all_ok:
        print("ALL CHECKS PASSED")
        return 0
    else:
        print("SOME CHECKS FAILED — fix and re-run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
