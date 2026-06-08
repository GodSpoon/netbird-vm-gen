# Ubuntu VM Deployment Tool — Comprehensive Plan

## Executive Summary

A technician-friendly, extensible tool for building and deploying Ubuntu 24.04 VMs to VMware vSphere and Microsoft Hyper-V. Each VM comes pre-configured with NetBird VPN (connected via setup key), NinjaOne RMM agent (downloaded via API), custom networking, hostname, and credentials — all driven by an interactive CLI prompt that any technician can run.

**Architecture Pattern**: Packer builds golden templates (one-time per hypervisor) → Python CLI tool deploys per-client VMs from those templates.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Project Structure](#2-project-structure)
3. [Stage 1: Packer Template Builder](#3-stage-1-packer-template-builder)
4. [Stage 2: Technician Deployment Tool](#4-stage-2-technician-deployment-tool)
5. [Interactive Prompt Flow](#5-interactive-prompt-flow)
6. [NetBird Integration](#6-netbird-integration)
7. [NinjaOne API Integration](#7-ninjaone-api-integration)
8. [Hypervisor Deployment Scripts](#8-hypervisor-deployment-scripts)
9. [Extensibility Design](#9-extensibility-design)
10. [Security Considerations](#10-security-considerations)
11. [Implementation Roadmap](#11-implementation-roadmap)
12. [Reference Configurations](#12-reference-configurations)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    TECHNICIAN WORKSTATION                        │
│                     (Windows Laptop)                              │
│                                                                  │
│  ┌──────────────────────────────────────────────────────┐       │
│  │          Python CLI Tool (vm-deploy.py)               │       │
│  │                                                      │       │
│  │  ┌─────────────┐  ┌──────────────┐  ┌───────────┐   │       │
│  │  │ Interactive │  │ NinjaOne API │  │ Config    │   │       │
│  │  │ Prompts     │→ │ Client       │→ │ Generator │   │       │
│  │  │ (rich/ui)   │  │ (OAuth v2)   │  │ (Jinja2)  │   │       │
│  │  └─────────────┘  └──────────────┘  └─────┬─────┘   │       │
│  │                                             │         │       │
│  │  ┌──────────────────────────────────────────┘         │       │
│  │  │         Platform-Specific Deployer                  │       │
│  │  │  ┌──────────────┐        ┌─────────────────┐       │       │
│  │  │  │ PowerCLI     │   OR   │ Hyper-V PS      │       │       │
│  │  │  │ (VMware)     │        │ (Hyper-V)       │       │       │
│  │  │  └──────────────┘        └─────────────────┘       │       │
│  │  └────────────────────────────────────────────────────┘       │
│  └──────────────────────────────────────────────────────┘       │
└──────────────────────────────┬───────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐     ┌──────────────┐
        │ NinjaOne │    │  NetBird │     │  Packer      │
        │ API v2   │    │  Cloud   │     │  Templates   │
        │ (cloud)  │    │  (cloud) │     │  (local)     │
        └──────────┘    └──────────┘     └──────┬───────┘
                                               │
                              ┌────────────────┼────────────────┐
                              │                │                │
                              ▼                ▼                ▼
                        ┌──────────┐    ┌──────────┐     ┌──────────┐
                        │ VMware   │    │  Hyper-V │     │  Config  │
                        │ vSphere  │    │  Server  │     │  Files   │
                        │ (clone)  │    │  (copy)  │     │  (YAML)  │
                        └──────────┘    └──────────┘     └──────────┘
```

### Build-Time vs Runtime Separation

| Phase | Tool | Frequency | Output |
|-------|------|-----------|--------|
| **Build** | Packer | Once per hypervisor (or when OS updates) | Golden template (VMware: template; Hyper-V: VHDX) |
| **Deploy** | Python CLI | Per-client, per-VM | Configured running VM |

This separation means:
- **Building the template takes ~20-30 minutes** (Packer installs Ubuntu from ISO)
- **Deploying from template takes ~2-5 minutes** (clone + customize + boot)
- Technicians never wait for OS installation

---

## 2. Project Structure

```
vm-deploy-tool/
├── README.md                           # Setup and usage guide
├── requirements.txt                    # Python dependencies
├── config.yaml                         # Global defaults (regions, URLs)
│
├── packer/                             # Stage 1: Template Building
│   ├── plugins.pkr.hcl                 # Required plugins
│   ├── variables.pkr.hcl               # Global variables
│   ├── build-vmware.pkr.hcl            # VMware vSphere builder
│   ├── build-hyperv.pkr.hcl            # Hyper-V builder
│   ├── http/                           # Autoinstall files served during build
│   │   ├── user-data.pkrtpl           # Template: Ubuntu autoinstall config
│   │   └── meta-data                  # Empty meta-data for NoCloud
│   └── scripts/
│       └── provision.sh               # Post-install cleanup & prep
│
├── deploy/                             # Stage 2: Deployment Tool
│   ├── vm-deploy.py                    # Main technician entry point
│   ├── lib/
│   │   ├── __init__.py
│   │   ├── prompts.py                  # Interactive CLI prompts
│   │   ├── config_builder.py           # Jinja2 user-data renderer
│   │   ├── ninjaone_client.py          # NinjaOne API v2 client
│   │   ├── netbird_installer.py        # NetBird install script gen
│   │   ├── vmware_deployer.py          # PowerCLI wrapper
│   │   └── hyperv_deployer.py          # Hyper-V PowerShell wrapper
│   └── templates/
│       ├── autoinstall.yaml.j2         # Jinja2: Ubuntu autoinstall
│       ├── netbird-setup.sh.j2         # Jinja2: NetBird install script
│       └── ninjaone-setup.sh.j2        # Jinja2: NinjaOne install script
│
├── scripts/                            # Standalone deployment scripts
│   ├── deploy-vmware.ps1              # PowerCLI: deploy from template
│   └── deploy-hyperv.ps1              # PowerShell: deploy from VHDX
│
└── examples/                           # Example configurations
    ├── client-acme-corp.yaml
    └── client-techstart.yaml
```

---

## 3. Stage 1: Packer Template Builder

### 3.1 Why Packer

Packer is the industry-standard tool for automated machine image creation [^6^][^10^]:
- **Multi-platform**: Same config builds for both VMware (vsphere-iso) and Hyper-V (hyperv-iso)
- **Unattended install**: Serves cloud-init user-data via HTTP or CD (cidata) [^48^][^51^]
- **Post-install provisioning**: Runs shell scripts after OS install to prepare the template
- **Reproducible**: Every build is identical — no configuration drift

### 3.2 Build Process

```
1. Packer starts VM from Ubuntu 24.04 ISO
2. Boot command enters kernel params: autoinstall ds=nocloud;s=/cdrom/
3. NoCloud datasource reads user-data + meta-data from CD (cidata label)
4. Ubuntu installs unattended (network, storage, user pre-configured)
5. Packer SSH connects to installed VM
6. Provisioning script runs: updates packages, installs guest agents, cleans up
7. Packer shuts down VM and converts to template
```

### 3.3 Packer Configuration (VMware)

```hcl
# packer/build-vmware.pkr.hcl
source "vsphere-iso" "ubuntu-2404" {
  vcenter_server       = var.vcenter_server
  username             = var.vcenter_username
  password             = var.vcenter_password
  insecure_connection  = true
  datacenter           = var.datacenter
  cluster              = var.cluster
  datastore            = var.datastore
  folder               = "Templates"

  vm_name              = "tpl-ubuntu-2404-base"
  guest_os_type        = "ubuntu64Guest"
  firmware             = "efi"

  CPUs                 = 2
  RAM                  = 4096
  disk_controller_type = ["pvscsi"]
  storage {
    disk_size             = 25600
    disk_thin_provisioned = true
  }
  network_adapters {
    network      = var.network
    network_card = "vmxnet3"
  }

  iso_paths = ["[${var.iso_datastore}] ${var.iso_path}"]

  # Serve autoinstall files via CD (most reliable)
  cd_files = ["./http/user-data", "./http/meta-data"]
  cd_label = "cidata"

  boot_wait = "5s"
  boot_command = [
    "c<wait>",
    "linux /casper/vmlinuz autoinstall ds=nocloud\\;s=/cdrom/ ---<enter>",
    "initrd /casper/initrd<enter>",
    "boot<enter>"
  ]

  ssh_username           = "packer"
  ssh_password           = "packer"
  ssh_timeout            = "60m"
  ssh_handshake_attempts = 100

  shutdown_command = "echo 'packer' | sudo -S shutdown -P now"
  convert_to_template = true
}
```

### 3.4 Packer Configuration (Hyper-V)

```hcl
# packer/build-hyperv.pkr.hcl
source "hyperv-iso" "ubuntu-2404" {
  vm_name     = "tpl-ubuntu-2404-base"
  guest_additions_mode = "disable"

  iso_url      = var.iso_url
  iso_checksum = var.iso_checksum

  boot_wait = "5s"
  boot_command = [
    "c<wait3s>",
    "linux /casper/vmlinuz autoinstall ds=nocloud\\;s=/cdrom/ ---<enter><wait3s>",
    "initrd /casper/initrd<enter><wait3s>",
    "boot<enter>"
  ]

  cd_files = ["./http/user-data", "./http/meta-data"]
  cd_label = "cidata"

  generation       = 2
  enable_secure_boot = true
  secure_boot_template = "MicrosoftUEFICertificateAuthority"

  cpus   = 2
  memory = 4096
  disk_size = 25600

  switch_name = var.hyperv_switch

  ssh_username = "packer"
  ssh_password = "packer"
  ssh_timeout  = "60m"

  shutdown_command = "echo 'packer' | sudo -S shutdown -P now"
}
```

### 3.5 Base Autoinstall (Template Build-Time)

The template build uses a **minimal** autoinstall — just enough to get a working VM. No client-specific data:

```yaml
# packer/http/user-data (template build version)
#cloud-config
autoinstall:
  version: 1
  source:
    id: ubuntu-server-minimal
  locale: en_US
  keyboard:
    layout: us
  network:
    version: 2
    ethernets:
      eth0:
        dhcp4: true
  storage:
    layout:
      name: lvm
      sizing-policy: all
  identity:
    hostname: ubuntu-template
    username: packer
    password: "$6$packer$ hashed_password_here"
  ssh:
    install-server: true
    allow-pw: true
  packages:
    - open-vm-tools         # VMware guest agent
    - hyperv-daemons        # Hyper-V guest agent
    - qemu-guest-agent      # Generic fallback
    - curl
    - wget
    - ca-certificates
    - gnupg
  user-data:
    disable_root: true
  late-commands:
    - curtin in-target -- apt-get update
    - curtin in-target -- apt-get install -y linux-virtual
    # Template cleanup (remove machine-specific data)
    - curtin in-target -- cloud-init clean --logs --seed
    - curtin in-target -- truncate -s 0 /etc/machine-id
    - curtin in-target -- rm -f /var/lib/dbus/machine-id
  shutdown: poweroff
```

**Build command:**
```bash
cd packer
packer init .
packer build -only=vsphere-iso.ubuntu-2404 .
# or
packer build -only=hyperv-iso.ubuntu-2404 .
```

---

## 4. Stage 2: Technician Deployment Tool

### 4.1 Design Philosophy

The deployment tool is a **single Python script** that:
1. **Prompts** the technician for all required values (interactive, friendly)
2. **Fetches** the NinjaOne agent installer via API
3. **Generates** client-specific autoinstall config (with NetBird + NinjaOne)
4. **Deploys** to the selected hypervisor using native tools

### 4.2 Entry Point

```bash
# Install once
pip install -r requirements.txt

# Run — prompts for everything
python deploy/vm-deploy.py

# Or with a saved client profile
python deploy/vm-deploy.py --profile configs/acme-corp.yaml

# Or non-interactive (for automation)
python deploy/vm-deploy.py \
  --hypervisor vmware \
  --client-name "Acme Corp" \
  --vm-name ACME-APP01 \
  --hostname acme-app01 \
  --ip 192.168.10.50/24 \
  --gateway 192.168.10.1 \
  --dns "8.8.8.8,1.1.1.1" \
  --cpu 2 \
  --ram 4096 \
  --netbird-setup-key "key-here" \
  --ninjaone-org "Acme Corp" \
  --ninjaone-location "Main Office" \
  --username admin \
  --password "SecurePass123!"
```

### 4.3 Python Dependencies

```txt
# requirements.txt
rich>=13.0          # Beautiful terminal UI and prompts
jinja2>=3.1         # Template rendering for user-data
requests>=2.31      # NinjaOne API HTTP client
pyyaml>=6.0         # Config file parsing
questionary>=2.0    # Interactive prompts (alternative to rich)
```

---

## 5. Interactive Prompt Flow

```
┌─────────────────────────────────────────────┐
│  Ubuntu VM Deployment Tool v1.0              │
│                                              │
│  Welcome! This wizard will guide you        │
│  through deploying a pre-configured         │
│  Ubuntu VM with NetBird + NinjaOne.         │
└─────────────────────────────────────────────┘

[1/8] Select Hypervisor
  > VMware vSphere
    Hyper-V

[2/8] VM Details
  VM Name: ACME-APP01
  Hostname: acme-app01
  Description: Application server for Acme Corp

[3/8] Hardware Configuration
  vCPUs: 2
  Memory (MB): 4096
  Disk Size (GB) [25]: 50

[4/8] Network Configuration
  IP Address: 192.168.10.50/24
  Gateway: 192.168.10.1
  DNS Servers (comma-separated): 8.8.8.8,1.1.1.1

[5/8] Admin Credentials
  Username: admin
  Password: ***********
  SSH Key (optional): ssh-ed25519 AAAAC3...

[6/8] NetBird VPN
  Setup Key: nb-skey-xxxxxxxxxxxxxxxx
  Management URL [https://api.netbird.io]:

[7/8] NinjaOne Agent
  Region [US]: US
  API Client ID: ninja-api-client-id
  API Client Secret: ***********

  Fetching organizations... done
  Select Organization:
    > Acme Corporation (ID: 1234)
      Beta Industries (ID: 5678)

  Fetching locations... done
  Select Location:
    > Main Office (ID: 456)
      Branch Office (ID: 789)

[8/8] Review and Deploy

  ┌─────────────────────────────────────┐
  │ DEPLOYMENT SUMMARY                  │
  │                                     │
  │ Hypervisor:    VMware vSphere       │
  │ VM Name:       ACME-APP01           │
  │ Hostname:      acme-app01           │
  │ IP:            192.168.10.50/24     │
  │ vCPUs/RAM:     2 / 4 GB             │
  │ NetBird:       Connected            │
  │ NinjaOne:      Acme Corp / Main     │
  │                                     │
  │ Deploy? [Y/n]:                      │
  └─────────────────────────────────────┘

  [14:32:05] Cloning template tpl-ubuntu-2404-base...
  [14:32:45] Generating autoinstall config...
  [14:32:46] Mounting config ISO...
  [14:32:48] Starting VM...
  [14:33:15] Waiting for VM to come online...
  [14:35:22] Verifying NetBird connection... OK
  [14:35:45] Verifying NinjaOne agent... OK
  [14:35:46] Deployment complete!
  
  VM Details:
    Name:   ACME-APP01
    IP:     192.168.10.50
    Status: Running
    SSH:    ssh admin@192.168.10.50
```

---

## 6. NetBird Integration

### 6.1 Installation Method

Use the **APT repository method** for reliability (not the one-liner curl pipe) [^6^]:

```bash
#!/bin/bash
# Generated script: netbird-setup.sh

set -euo pipefail

NETBIRD_SETUP_KEY="{{ netbird_setup_key }}"
NETBIRD_MANAGEMENT_URL="{{ netbird_management_url | default('https://api.netbird.io') }}"
VM_HOSTNAME="{{ hostname }}"

# Install prerequisites
apt-get update
apt-get install -y -q ca-certificates curl gnupg

# Add NetBird GPG key
curl -sSL https://pkgs.netbird.io/debian/public.key | \
  gpg --dearmor -o /usr/share/keyrings/netbird-archive-keyring.gpg

# Add repository
echo 'deb [signed-by=/usr/share/keyrings/netbird-archive-keyring.gpg] \
  https://pkgs.netbird.io/debian stable main' | \
  tee /etc/apt/sources.list.d/netbird.list

# Install NetBird (CLI only, no UI for server)
apt-get update
apt-get install -y -q netbird

# Enable and start service
systemctl enable --now netbird

# Connect with setup key
netbird up \
  --setup-key "$NETBIRD_SETUP_KEY" \
  --management-url "$NETBIRD_MANAGEMENT_URL" \
  --hostname "$VM_HOSTNAME"

# Verify connection
sleep 5
if netbird status | grep -q "Connected"; then
    echo "[OK] NetBird connected"
else
    echo "[WARN] NetBird status check failed"
fi
```

### 6.2 Autoinstall Integration

NetBird is installed via `late-commands` during the Ubuntu installation:

```yaml
# In the rendered autoinstall user-data
late-commands:
  # Write the NetBird setup script into the target system
  - |
    cat > /target/opt/netbird-setup.sh << 'SCRIPT'
    #!/bin/bash
    # [NetBird setup script contents from above]
    SCRIPT
  - chmod +x /target/opt/netbird-setup.sh
  - curtin in-target -- /opt/netbird-setup.sh
```

### 6.3 Setup Key Types

| Type | Use Case |
|------|----------|
| **Reusable** | Use one key for all VMs at a client (simpler) |
| **One-off** | Generate per-VM for maximum security |
| **Ephemeral** | Auto-removes peer after going offline (for temp VMs) |

---

## 7. NinjaOne API Integration

### 7.1 Authentication Flow

```python
# deploy/lib/ninjaone_client.py
import requests
from typing import Optional, List, Dict

class NinjaOneClient:
    REGIONS = {
        'US':  'app.ninjarmm.com',
        'US2': 'us2.ninjarmm.com',
        'EU':  'eu.ninjarmm.com',
        'CA':  'ca.ninjarmm.com',
        'OC':  'oc.ninjarmm.com',
    }

    def __init__(self, client_id: str, client_secret: str, region: str = 'US'):
        self.base_url = f"https://{self.REGIONS[region]}"
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: Optional[str] = None

    def authenticate(self) -> str:
        """OAuth 2.0 Client Credentials flow."""
        resp = requests.post(
            f"{self.base_url}/ws/oauth/token",
            data={
                'grant_type': 'client_credentials',
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'scope': 'monitoring management'
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        resp.raise_for_status()
        self._token = resp.json()['access_token']
        return self._token

    def get_organizations(self) -> List[Dict]:
        """List all organizations accessible to this API client."""
        resp = requests.get(
            f"{self.base_url}/api/v2/organizations",
            headers={'Authorization': f'Bearer {self._token}'}
        )
        resp.raise_for_status()
        return resp.json()

    def get_locations(self, org_id: int) -> List[Dict]:
        """List locations for an organization."""
        resp = requests.get(
            f"{self.base_url}/api/v2/organization/{org_id}/locations",
            headers={'Authorization': f'Bearer {self._token}'}
        )
        resp.raise_for_status()
        return resp.json()

    def get_installer_url(self, org_id: int, location_id: int,
                          installer_type: str = 'LINUX_DEB') -> str:
        """Generate a time-limited installer download URL."""
        resp = requests.get(
            f"{self.base_url}/v2/organization/{org_id}/location/{location_id}/installer/{installer_type}",
            headers={'Authorization': f'Bearer {self._token}'}
        )
        resp.raise_for_status()
        return resp.json()['url']
```

### 7.2 Installation Method

The NinjaOne agent is installed by downloading the DEB from the API-generated URL and installing with `dpkg` [^1^][^20^]:

```bash
#!/bin/bash
# Generated script: ninjaone-setup.sh

INSTALLER_URL="{{ ninjaone_installer_url }}"
DEB_FILE="/tmp/ninjarmm-agent.deb"

echo "[INFO] Downloading NinjaOne agent..."
wget -q -O "$DEB_FILE" "$INSTALLER_URL"

echo "[INFO] Installing NinjaOne agent..."
dpkg -i "$DEB_FILE"
apt-get install -f -y 2>/dev/null || true

echo "[INFO] Starting NinjaOne agent..."
systemctl daemon-reload
systemctl enable --now ninjarmm-agent 2>/dev/null || systemctl enable --now ninjaone-agent

# Cleanup
rm -f "$DEB_FILE"

echo "[OK] NinjaOne agent installed"
```

### 7.3 Alternative: Generic Installer with Token

If API access is unavailable, use the generic installer URL with a TOKENID:

```bash
# Download generic DEB
curl -L -o /tmp/NinjaOneAgent-x86_64.deb \
  "https://app.ninjarmm.com/ws/api/v2/generic-installer/NinjaOneAgent-x86_64.deb"

# Install with token (the token ties it to your org)
sudo TOKENID="your-installer-token" dpkg -i /tmp/NinjaOneAgent-x86_64.deb
```

---

## 8. Hypervisor Deployment Scripts

### 8.1 VMware vSphere (PowerCLI)

```powershell
# scripts/deploy-vmware.ps1
param(
    [Parameter(Mandatory)] [string] $VMName,
    [Parameter(Mandatory)] [string] $TemplateName,
    [Parameter(Mandatory)] [string] $VMHost,
    [Parameter(Mandatory)] [string] $Datastore,
    [string] $Folder = "",
    [int] $CPU = 2,
    [int] $MemoryMB = 4096,
    [string] $NetworkName = "VM Network",
    [string] $CustomizationSpecName = "",
    [string] $ConfigIsoPath = ""  # Path to client-specific autoinstall ISO
)

# Connect to vCenter
Connect-VIServer -Server $env:VCENTER_SERVER -Credential (Get-Credential)

# Clone from template
$template = Get-Template -Name $TemplateName

# Create VM from template
$vm = New-VM -Name $VMName `
    -Template $template `
    -VMHost (Get-VMHost -Name $VMHost) `
    -Datastore (Get-Datastore -Name $Datastore) `
    -Location (Get-Folder -Name $Folder -ErrorAction SilentlyContinue)

# Set hardware
Set-VM -VM $vm -NumCpu $CPU -MemoryMB $MemoryMB -Confirm:$false

# Configure network
$adapter = Get-NetworkAdapter -VM $vm
Set-NetworkAdapter -NetworkAdapter $adapter `
    -Portgroup (Get-VirtualPortGroup -Name $NetworkName) -Confirm:$false

# Mount config ISO if provided (for autoinstall reconfiguration)
if ($ConfigIsoPath) {
    New-CDDrive -VM $vm -IsoPath $ConfigIsoPath -StartConnected:$true
}

# Power on
Start-VM -VM $vm

Write-Host "VM '$VMName' deployed successfully."

# Optionally wait for IP
$ip = $vm.Guest.IPAddress | Select-Object -First 1
Write-Host "VM IP: $ip"
```

### 8.2 Hyper-V (PowerShell)

```powershell
# scripts/deploy-hyperv.ps1
param(
    [Parameter(Mandatory)] [string] $VMName,
    [Parameter(Mandatory)] [string] $TemplateVhdx,
    [Parameter(Mandatory)] [string] $VMSwitch,
    [string] $VMPath = "C:\VMs",
    [int] $CPU = 2,
    [long] $MemoryBytes = 4GB,
    [long] $MinMemoryBytes = 1GB,
    [long] $MaxMemoryBytes = 8GB,
    [string] $ConfigIsoPath = ""
)

# Create VM directory
$vmDir = Join-Path $VMPath $VMName
New-Item -ItemType Directory -Path $vmDir -Force | Out-Null

# Create differencing disk from template
$vhdPath = Join-Path $vmDir "$VMName.vhdx"
New-VHD -Path $vhdPath -ParentPath $TemplateVhdx -Differencing

# Create VM
$vm = New-VM -Name $VMName `
    -Path $vmDir `
    -VHDPath $vhdPath `
    -Generation 2 `
    -MemoryStartupBytes $MemoryBytes `
    -SwitchName $VMSwitch

# Configure processor and memory
Set-VMProcessor -VMName $VMName -Count $CPU
Set-VMMemory -VMName $VMName `
    -DynamicMemoryEnabled $true `
    -MinimumBytes $MinMemoryBytes `
    -StartupBytes $MemoryBytes `
    -MaximumBytes $MaxMemoryBytes

# Mount config ISO if provided
if ($ConfigIsoPath) {
    Add-VMDvdDrive -VMName $VMName -Path $ConfigIsoPath
}

# Enable guest services
Enable-VMIntegrationService -Name "Guest Service Interface" -VMName $VMName

# Start VM
Start-VM -Name $VMName

Write-Host "VM '$VMName' deployed on Hyper-V."
```

### 8.3 Post-Deploy Reconfiguration (Both Platforms)

Since the template is built without client-specific data, reconfiguration happens post-deployment using **cloud-init with a secondary config ISO**:

```python
# Python: Generate a cloud-init config ISO to mount to the deployed VM
def create_config_iso(vm_config: dict, output_path: str):
    """Create a cidata ISO with client-specific cloud-init config."""
    import tempfile, subprocess, os

    with tempfile.TemporaryDirectory() as tmp:
        # Render user-data with client-specific config
        user_data = render_autoinstall(vm_config)

        # Write files
        with open(f"{tmp}/user-data", 'w') as f:
            f.write(user_data)
        with open(f"{tmp}/meta-data", 'w') as f:
            f.write(f"instance-id: {vm_config['hostname']}\n")
            f.write(f"local-hostname: {vm_config['hostname']}\n")

        # Create ISO with cidata label
        subprocess.run([
            'mkisofs', '-o', output_path,
            '-V', 'cidata',
            '-J', '-R',
            f"{tmp}/user-data", f"{tmp}/meta-data"
        ], check=True)

    return output_path
```

**Alternative: Inject config into VHDX before first boot (Hyper-V)**

For Hyper-V, mount the VHDX and write cloud-init files directly before starting the VM:

```powershell
# Mount VHDX
$vhd = Mount-DiskImage -ImagePath $vhdPath -Passthru
$driveLetter = ($vhd | Get-Volume).DriveLetter

# Write cloud-init files
Copy-Item "client-user-data" "${driveLetter}:\user-data"
Copy-Item "client-meta-data" "${driveLetter}:\meta-data"

# Dismount
Dismount-DiskImage -ImagePath $vhdPath
```

---

## 9. Extensibility Design

### 9.1 Plugin Architecture

New agents/services can be added by creating a new Jinja2 template in `deploy/templates/` and adding a prompt section:

```python
# deploy/lib/prompts.py — extensible prompt registry

INSTALLERS = {
    'netbird': {
        'name': 'NetBird VPN',
        'template': 'netbird-setup.sh.j2',
        'prompts': [
            {'key': 'setup_key', 'prompt': 'NetBird Setup Key', 'type': 'password'},
            {'key': 'management_url', 'prompt': 'Management URL', 'default': 'https://api.netbird.io'},
        ]
    },
    'ninjaone': {
        'name': 'NinjaOne Agent',
        'template': 'ninjaone-setup.sh.j2',
        'prompts': [
            {'key': 'client_id', 'prompt': 'NinjaOne API Client ID', 'type': 'password'},
            {'key': 'client_secret', 'prompt': 'NinjaOne API Client Secret', 'type': 'password'},
            {'key': 'region', 'prompt': 'Region', 'default': 'US'},
            {'key': 'organization', 'prompt': 'Organization'},
            {'key': 'location', 'prompt': 'Location'},
        ]
    },
    # ADD NEW AGENTS HERE
    # 'datto': {
    #     'name': 'Datto RMM Agent',
    #     'template': 'datto-setup.sh.j2',
    #     'prompts': [...]
    # },
}
```

### 9.2 Adding a New Agent (Step-by-Step)

1. **Create template**: `deploy/templates/myagent-setup.sh.j2`
2. **Register in `prompts.py`**: Add entry to `INSTALLERS` dict
3. **Add API client** (if needed): `deploy/lib/myagent_client.py`
4. **Done** — the tool automatically includes it in prompts and autoinstall

### 9.3 Client Profiles

Save common client configurations for one-click deployment:

```yaml
# examples/client-acme-corp.yaml
client_name: "Acme Corporation"
hypervisor: vmware
vcenter_server: vc01.acme.local
cluster: ACME-CL01
datastore: ACME-DS01
network: ACME-VLAN10

netbird:
  setup_key: "nb-skey-xxxxxxxxxxxxxxxx"
  management_url: "https://api.netbird.io"

ninjaone:
  region: US
  client_id: "ninja-api-xxxxxxxx"
  client_secret: "${NINJA_ACME_SECRET}"  # Resolved from env var
  organization: "Acme Corporation"
  location: "Main Office"

defaults:
  cpu: 2
  memory: 4096
  disk: 50
  username: admin
  dns: [8.8.8.8, 1.1.1.1]
  domain: acme.local
```

Usage:
```bash
python deploy/vm-deploy.py --profile examples/client-acme-corp.yaml
# Only prompts for: VM name, hostname, IP address, password
```

---

## 10. Security Considerations

### 10.1 Credential Handling

| Secret | Storage | Notes |
|--------|---------|-------|
| vCenter credentials | Environment variables (`VCENTER_USER`, `VCENTER_PASS`) | Never in files |
| NinjaOne API secrets | Environment variables or keyring | Profile files use `${ENV_VAR}` syntax |
| NetBird setup keys | Prompted at runtime, not logged | Marked as password type in prompts |
| VM admin passwords | Prompted at runtime | SHA-512 hashed in autoinstall |
| SSH private keys | Filesystem (chmod 600) | Optional alternative to passwords |

### 10.2 Password Hashing

The tool hashes passwords client-side before embedding in autoinstall:

```python
import crypt, secrets

def hash_password(plain: str) -> str:
    """Generate SHA-512 password hash for autoinstall."""
    salt = crypt.mksalt(crypt.METHOD_SHA512)
    return crypt.crypt(plain, salt)
```

### 10.3 API Token Lifecycle

- NinjaOne tokens are **short-lived** (1 hour expiry)
- Token is obtained at runtime, used immediately, not stored
- Client ID/Secret are stored in environment variables or OS keyring

---

## 11. Implementation Roadmap

### Phase 1: MVP (Week 1-2) — Core Deployment

| Task | Effort | Deliverable |
|------|--------|-------------|
| Packer configs for VMware + Hyper-V | 1 day | Working golden templates |
| Python CLI with interactive prompts | 1 day | vm-deploy.py entry point |
| NinjaOne API client | 1 day | Organization/location/installer download |
| NetBird install script template | 0.5 day | Jinja2 template |
| NinjaOne install script template | 0.5 day | Jinja2 template |
| Autoinstall YAML renderer | 1 day | Full user-data generation |
| VMware deployment (PowerCLI wrapper) | 1 day | Clone + customize |
| Hyper-V deployment (PS wrapper) | 1 day | Differencing disk + create |
| Integration testing | 2 days | End-to-end deploy to both hypervisors |

### Phase 2: Polish (Week 3) — Technician Experience

| Task | Effort | Deliverable |
|------|--------|-------------|
| Rich terminal UI (progress bars, colors) | 1 day | Beautiful output |
| Client profile save/load | 1 day | `--profile` flag |
| Validation (IP format, connectivity tests) | 1 day | Pre-flight checks |
| Post-deploy verification | 0.5 day | Confirm NetBird + NinjaOne |
| README + documentation | 1.5 day | Technician guide |

### Phase 3: Extensibility (Week 4) — Future-Proof

| Task | Effort | Deliverable |
|------|--------|-------------|
| Plugin registry system | 1 day | Add agents without code changes |
| Terraform alternative deployment | 2 days | Optional Terraform mode |
| CI/CD pipeline (GitHub Actions) | 1 day | Auto-build templates on ISO update |
| Windows VM support | 2 days | Windows Server template variant |

---

## 12. Reference Configurations

### 12.1 Complete Autoinstall Template (Jinja2)

```yaml
{# deploy/templates/autoinstall.yaml.j2 #}
#cloud-config
autoinstall:
  version: 1
  source:
    id: ubuntu-server-minimal
  locale: en_US
  keyboard:
    layout: us
  network:
    version: 2
    ethernets:
      eth0:
        dhcp4: {% if network.dhcp %}true{% else %}false{% endif %}
        {% if not network.dhcp %}
        addresses:
          - {{ network.ip_address }}/{{ network.cidr_prefix }}
        routes:
          - to: default
            via: {{ network.gateway }}
        nameservers:
          addresses:
            {% for dns in network.dns_servers %}
            - {{ dns }}
            {% endfor %}
          {% if network.search_domain %}
          search:
            - {{ network.search_domain }}
          {% endif %}
        {% endif %}
  storage:
    layout:
      name: lvm
      sizing-policy: all
  identity:
    hostname: {{ hostname }}
    username: {{ admin.username }}
    password: "{{ admin.password_hash }}"
    {% if admin.realname %}
    realname: {{ admin.realname }}
    {% endif %}
  ssh:
    install-server: true
    allow-pw: {{ 'true' if admin.allow_password else 'false' }}
    {% if admin.ssh_authorized_keys %}
    authorized-keys:
      {% for key in admin.ssh_authorized_keys %}
      - "{{ key }}"
      {% endfor %}
    {% endif %}
  packages:
    - curl
    - wget
    - ca-certificates
    - gnupg
    - open-vm-tools
    - hyperv-daemons
  user-data:
    disable_root: true
  late-commands:
    # Write setup scripts into target
    - |
      cat > /target/opt/00-netbird.sh << 'EOF'
{{ netbird_script | indent(6) }}
      EOF
      chmod +x /target/opt/00-netbird.sh
    - |
      cat > /target/opt/01-ninjaone.sh << 'EOF'
{{ ninjaone_script | indent(6) }}
      EOF
      chmod +x /target/opt/01-ninjaone.sh
    # Run setup scripts in target
    - curtin in-target -- /opt/00-netbird.sh
    - curtin in-target -- /opt/01-ninjaone.sh
    # Cleanup
    - curtin in-target -- rm -f /opt/00-netbird.sh /opt/01-ninjaone.sh
  shutdown: reboot
```

### 12.2 Minimal Working Example

```bash
# Quick start: Deploy a VM in 5 commands

# 1. Install Python deps
pip install rich jinja2 requests pyyaml

# 2. Set secrets
export VCENTER_SERVER=vc01.lab.local
export VCENTER_USER=administrator@vsphere.local
export VCENTER_PASS='YourPassword'
export NINJA_CLIENT_ID=your-ninja-client-id
export NINJA_CLIENT_SECRET=your-ninja-client-secret

# 3. Run the deploy tool
python deploy/vm-deploy.py \
  --hypervisor vmware \
  --vm-name CLIENT-VM01 \
  --hostname client-vm01 \
  --ip 192.168.1.100/24 \
  --gateway 192.168.1.1 \
  --dns "8.8.8.8" \
  --cpu 2 --ram 4096 \
  --netbird-setup-key "nb-skey-abc123" \
  --ninjaone-org "Client Name" \
  --ninjaone-location "Main Office" \
  --username admin \
  --password "SecurePass123!"

# 4. Wait 3-5 minutes

# 5. Connect
ssh admin@192.168.1.100
```

---

## Key Research Sources

| Topic | Source | Relevance |
|-------|--------|-----------|
| Packer + VMware | [tekanaid.com](https://tekanaid.com/posts/packer-on-ubuntu-2404-for-vsphere) | Packer vSphere-iso builder with cloud-init |
| Packer + Hyper-V | [developer.hashicorp.com](https://developer.hashicorp.com/packer/integrations/hashicorp/hyperv/latest/components/builder/iso) | Hyper-V ISO builder documentation |
| Ubuntu Autoinstall | [canonical/subiquity](https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html) | Official autoinstall YAML reference |
| NetBird Silent Install | [docs.netbird.io](https://docs.netbird.io/how-to/installation) | Setup keys, headless install, env vars |
| NinjaOne API v2 | [app.ninjarmm.com/apidocs-beta](https://app.ninjarmm.com/apidocs-beta/) | OAuth flow, organizations, installer endpoint |
| NinjaOne Universal Installer | [github/genericService](https://github.com/genericService/ninjaone-universal-installer) | Complete PowerShell implementation |
| PowerCLI Template Clone | [Broadcom docs](https://docs.broadcom.com/doc/12905-PowerCLI-133) | New-VM, OSCustomizationSpec |
| Hyper-V Differencing Disks | [Microsoft Learn](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vhd) | New-VHD -Differencing |
