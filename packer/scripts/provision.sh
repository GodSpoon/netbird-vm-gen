#!/bin/bash
set -euo pipefail

echo "==> Updating package lists and upgrading installed packages..."
apt-get update
apt-get upgrade -y

echo "==> Installing guest agents (if missing)..."
apt-get install -y open-vm-tools hyperv-daemons qemu-guest-agent || true

echo "==> Cleaning apt cache..."
apt-get autoremove -y
apt-get clean

echo "==> Zeroing free space..."
if command -v fstrim >/dev/null 2>&1; then
  fstrim -av || true
else
  dd if=/dev/zero of=/zero.fill bs=1M || true
  rm -f /zero.fill
  sync
fi

echo "==> Provisioning complete."
