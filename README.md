# netbird-vm-gen

A two-stage deployment tool for building and provisioning Ubuntu 24.04 VMs with NetBird VPN and NinjaOne RMM pre-installed. Stage 1 uses Packer to create golden templates for VMware vSphere and Microsoft Hyper-V. Stage 2 is a Python CLI that collects per-VM configuration and deploys a running machine in minutes.

Technicians run the CLI interactively; automation pipelines can call it non-interactively with flags or YAML profiles.

---

## Architecture

```
Build (one-time)          Runtime (per VM)
┌─────────────┐           ┌─────────────────────────┐
│   Packer    │ ──►       │  python deploy/vm_deploy.py  │
│  ISO ──►    │  Template │        │                │
│  Template   │           │   ┌────┴────┐           │
└─────────────┘           │   │ Prompts │           │
                          │   └───┬─────┘           │
                          │   ┌───┴───┐   ┌────────┐│
                          │   │ Ninja │   │ Config ││
                          │   │  One  │   │Builder ││
                          │   └───┬───┘   └───┬────┘│
                          │   ┌───┴───────────┘     │
                          │   │ Deployer            │
                          │   │ (PowerCLI / Hyper-V)│
                          │   └─────────┬───────────┘
                          └─────────────┼─────────────┘
                                        ▼
                                   Running VM
```

| Phase | Duration | Frequency |
|-------|----------|-----------|
| **Build** (Packer) | ~20–30 min | Once per hypervisor, or when the OS image changes |
| **Deploy** (Python CLI) | ~2–5 min | Once per VM |

The template build installs Ubuntu from ISO, applies updates, installs guest agents, and scrubs machine-specific state. Every deployed VM starts from that clean baseline and is customized with client-specific networking, credentials, NetBird setup, and NinjaOne registration.

---

## Prerequisites

| Requirement | Version / Notes |
|-------------|-----------------|
| Python | 3.10+ |
| Packer | 1.9+ (only for building golden templates) |
| VMware PowerCLI | Required for vSphere deployments (`Install-Module VMware.PowerCLI`) |
| Hyper-V role | Required for Hyper-V deployments (Windows feature) |
| NinjaOne API credentials | Client ID + Secret from your NinjaOne tenant |
| `keyring` Python package | Optional — for secure credential storage (`pip install keyring`) |
| NetBird setup key | From your NetBird management console |
| Ubuntu 24.04 Server ISO | Download from [ubuntu.com](https://ubuntu.com/download/server) |

---

## Installation

```bash
git clone https://github.com/GodSpoon/netbird-vm-gen.git
cd netbird-vm-gen
pip install -r requirements.txt
```

Dependencies are listed in `requirements.txt`:
- `rich>=13.0`
- `jinja2>=3.1`
- `requests>=2.31`
- `pyyaml>=6.0`
- `questionary>=2.0`

---

## Building Golden Templates (Packer)

The Packer configs are in the `packer/` directory. You only need to run this once per hypervisor.

```bash
cd packer
packer init .
```

**VMware vSphere:**
```bash
packer build -only=vsphere-iso.ubuntu-2404 .
```

**Microsoft Hyper-V:**
```bash
packer build -only=hyperv-iso.ubuntu-2404 .
```

- Set variables via `-var` flags, a `*.pkrvars.hcl` file, or environment variables. See `packer/variables.pkr.hcl` for the full list.
- The VMware builder produces a vSphere template. The Hyper-V builder produces a VHDX file.
- Re-run Packer when you want to update the base OS, kernel, or guest agents.

---

## Environment Variables

The deployment tool and PowerShell wrappers read secrets and connection details from the environment. Do not commit credentials to the repository.

| Variable | Purpose | Required By |
|----------|---------|-------------|
| `VCENTER_SERVER` | vCenter hostname or IP | VMware deployments |
| `VCENTER_USER` | vCenter username | VMware deployments |
| `VCENTER_PASS` | vCenter password | VMware deployments |
| `NINJA_CLIENT_ID` | NinjaOne API Client ID | NinjaOne agent install |
| `NINJA_CLIENT_SECRET` | NinjaOne API Client Secret | NinjaOne agent install |
| `NETBIRD_SETUP_KEY` | NetBird setup key | NetBird VPN join (optional; tool can prompt) |

Set them in your shell profile, a `.env` file (not tracked by git), or pass them inline for automation:

```bash
export VCENTER_SERVER=vc01.lab.local
export VCENTER_USER=administrator@vsphere.local
export VCENTER_PASS='YourPassword'
export NINJA_CLIENT_ID=your-ninja-client-id
export NINJA_CLIENT_SECRET=your-ninja-client-secret
export NETBIRD_SETUP_KEY=nb-skey-xxxxxxxxxxxxxxxx
```

Profile YAML files support `${ENV_VAR}` syntax for secrets so they are not hardcoded on disk. See the **Client Profiles** section below.

## Config Layering

The tool merges configuration from four sources, with later sources overriding earlier ones:

1. `config.yaml` — Global defaults (username, DNS, NinjaOne region, custom base URL)
2. Profile YAML — Per-client defaults loaded with `--profile`
3. Environment variables — `${ENV_VAR}` syntax in profile YAML
4. CLI flags — Override everything

Edit `config.yaml` to set your organization's static defaults:
```yaml
ninjaone:
  base_url: "https://4eos.rmmservices.net"
  default_region: "US"
defaults:
  username: admin
  dns:
    - 8.8.8.8
    - 1.1.1.1
```

## NinjaOne API Setup

Before you can deploy the NinjaOne agent, you need API credentials:

1. Go to **Administration → Apps → API** in your NinjaOne portal
2. Click **"Add client app"**
3. Grant scopes: **Monitoring, Management, Control, Refresh Token**
4. Copy the **Client ID** and **Client Secret**

Your NinjaOne portal may use a custom URL (e.g. `https://4eos.rmmservices.net`) while still using the US API region. Set it in `config.yaml` or with `--ninjaone-base-url`.

---

## Interactive Deployment

Run the wizard and answer the prompts:

```bash
python deploy/vm_deploy.py
```

The wizard walks through 8 steps:

1. **Select Hypervisor** — VMware vSphere or Hyper-V
2. **VM Details** — Name, hostname, description
3. **Hardware** — vCPUs, memory (MB), disk (GB)
4. **Network** — Static IP with CIDR, gateway, DNS servers, search domain
5. **Admin Credentials** — Username, password, optional SSH public key
6. **NetBird VPN** — Setup key, management URL
7. **NinjaOne Agent** — Region, API Client ID/Secret, organization, location
8. **Review and Deploy** — Confirmation table; proceeds to clone template, generate autoinstall config, mount cidata ISO, and power on the VM

Cancel at any prompt with `Ctrl+C`.

---

## Non-Interactive Deployment

Pass all required values as flags for CI/CD or scripting:

```bash
python deploy/vm_deploy.py \
  --hypervisor vmware \
  --profile examples/client-acme-corp.yaml \
  --vm-name netbird-acme-corp-01 \
  --hostname acme-app01 \
  --ip 192.168.10.50/24 \
  --gateway 192.168.10.1 \
  --dns "8.8.8.8,1.1.1.1" \
  --cpu 2 \
  --ram 4096 \
  --disk 50 \
  --username admin \
  --password "SecurePass123!" \
  --netbird-setup-key "nb-skey-xxxxxxxxxxxxxxxx" \
  --ninjaone-base-url "https://4eos.rmmservices.net" \
  --ninjaone-org-id 9 \
  --ninjaone-location-id 9 \
  --save-credentials
```

Available flags:

| Flag | Description |
|------|-------------|
| `--hypervisor {vmware,hyper-v}` | Target hypervisor |
| `--profile PATH` | Load defaults from a YAML client profile |
| `--vm-name NAME` | VM name |
| `--hostname HOST` | Guest OS hostname |
| `--ip CIDR` | Static IP address with CIDR (e.g. `192.168.1.10/24`) |
| `--gateway IP` | Default gateway |
| `--dns SERVERS` | Comma-separated DNS servers |
| `--cpu N` | Number of vCPUs |
| `--ram MB` | Memory in megabytes |
| `--disk GB` | Disk size in gigabytes |
| `--username USER` | Admin username |
| `--password PASS` | Admin password |
| `--ssh-key KEY` | SSH public key string (optional) |
| `--netbird-setup-key KEY` | NetBird setup key |
| `--netbird-mgmt-url URL` | NetBird management URL (default: `https://api.netbird.io`) |
| `--ninjaone-base-url URL` | NinjaOne custom portal URL |
| `--ninjaone-org-id ID` | NinjaOne organization ID |
| `--ninjaone-location-id ID` | NinjaOne location ID |
| `--ninjaone-org-name NAME` | NinjaOne organization name |
| `--ninjaone-location-name NAME` | NinjaOne location name |
| `--ninjaone-org ORG` | NinjaOne organization (legacy) |
| `--ninjaone-location LOC` | NinjaOne location (legacy) |
| `--save-credentials` | Store NinjaOne API creds in OS keyring |
| `--save-profile PATH` | Save resolved config as a new profile YAML |
| `--dry-run` | Render configs without deploying |

Values provided via flags override the profile; anything still missing falls back to the interactive prompts.

## Organization & Location Selection

You can specify NinjaOne org/location by **ID** or **name**:

| Method | Flag | Example |
|--------|------|---------|
| Numeric ID | `--ninjaone-org-id` / `--ninjaone-location-id` | `--ninjaone-org-id 9` |
| Exact name | `--ninjaone-org-name` / `--ninjaone-location-name` | `--ninjaone-org-name "Acme Corp"` |
| Legacy | `--ninjaone-org` / `--ninjaone-location` | `--ninjaone-org "Acme Corp"` |

If only one org/location exists, it is auto-selected. If multiple exist and none is specified, the tool errors with a list of available choices.

## VM Naming Convention

The default naming pattern is `netbird-{company}-{index:02d}`:

```bash
netbird-acme-corp-01
netbird-acme-corp-02
netbird-techstart-03
```

The `{company}` token is derived from the profile's `client_name` or the NinjaOne organization name. Special characters and spaces are sanitized. The index auto-increments based on existing VMs.

## Secure Credential Storage

Store NinjaOne API credentials in your OS credential store (Windows Credential Manager, macOS Keychain, or Linux Secret Service):

```bash
python deploy/vm_deploy.py --save-credentials \
  --ninjaone-client-id "your-id" \
  --ninjaone-client-secret "your-secret" \
  ...
```

On subsequent runs, credentials are loaded automatically — no need to pass them every time:
```bash
python deploy/vm_deploy.py --profile examples/client-acme-corp.yaml ...
```

---

## Client Profiles

Save recurring client settings in a YAML profile to pre-fill the wizard or non-interactive run. Use `${ENV_VAR}` for secrets.

```yaml
# examples/client-acme-corp.yaml
client_name: acme-corp
hypervisor: vmware
vcenter_server: vc01.acme.local
cluster: ACME-CL01
datastore: ACME-DS01
network: ACME-VLAN10

netbird:
  setup_key: nb-skey-acme-fake123
  management_url: https://api.netbird.io

ninjaone:
  region: US
  client_id: ${ACME_NINJA_CLIENT_ID}
  client_secret: ${ACME_NINJA_CLIENT_SECRET}
  organization: "Acme Corporation"
  location: "Main Office"

defaults:
  cpu: 2
  memory: 4096
  disk: 50
  username: admin
  dns:
    - 8.8.8.8
    - 1.1.1.1
  domain: acme.local
```

Usage:

```bash
# Interactive with profile pre-fill
python deploy/vm_deploy.py --profile examples/client-acme-corp.yaml

# Non-interactive with profile + overrides
python deploy/vm_deploy.py \
  --profile examples/client-acme-corp.yaml \
  --vm-name ACME-DB01 \
  --hostname acme-db01 \
  --ip 192.168.10.51/24
```

Profile keys map to wizard fields; missing fields are still prompted interactively unless all required values are supplied via flags or the profile.
### Saving a profile from a deployment

After a successful deployment, save the resolved config as a reusable profile:
```bash
python deploy/vm_deploy.py \
  --profile examples/client-acme-corp.yaml \
  --vm-name ACME-DB01 \
  --save-profile examples/client-acme-corp-updated.yaml
```

---

## Extensibility: Adding a New Agent

The tool uses an `INSTALLERS` registry in `deploy/lib/prompts.py` to know which agents to prompt for and which Jinja2 template to render. To add a new agent (for example, Datto RMM):

1. **Create the setup script template:**
   ```bash
   # deploy/templates/datto-setup.sh.j2
   ```
   Write a standard shell script using Jinja2 variables (`{{ datto_token }}`, etc.).

2. **Register the agent in `deploy/lib/prompts.py`:**
   ```python
   INSTALLERS = {
       "netbird": { ... },
       "ninjaone": { ... },
       "datto": {
           "name": "Datto RMM Agent",
           "template": "datto-setup.sh.j2",
           "prompts": [
               {"key": "token", "prompt": "Datto Token", "type": "password"},
               {"key": "site", "prompt": "Site ID"},
           ],
       },
   }
   ```

3. **Wire rendering (if needed):**
   If the agent requires an API client, add `deploy/lib/datto_client.py` and import it from `deploy/vm_deploy.py`.

4. **Update the autoinstall template (if necessary):**
   `deploy/templates/autoinstall.yaml.j2` loops over scripts dynamically; as long as the new script is emitted into the rendered output, it will run during `late-commands`.

No other code changes are required. The wizard automatically includes the new prompts and the config builder includes the rendered script in the cloud-init payload.

---

## Security

- **No hardcoded credentials.** All secrets are supplied via environment variables, interactive password prompts, or `${ENV_VAR}` resolution in profile YAML.
- **Password hashing.** Admin passwords are hashed with SHA-512 (`crypt.mksalt(crypt.METHOD_SHA512)`) before being embedded in the autoinstall config. The plaintext password never appears in generated files or logs.
- **Short-lived API tokens.** NinjaOne OAuth tokens are obtained at runtime, kept in memory only, and are not written to disk.
- **Setup keys are masked.** NetBird and NinjaOne secrets are rendered as `••••••••` in the review table.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `VMware.PowerCLI module is not installed` | PowerCLI missing or not imported | `Install-Module -Name VMware.PowerCLI -Scope CurrentUser` |
| `PowerShell (pwsh or powershell) is not available` | PowerShell not on PATH | Install PowerShell 7+ or ensure `pwsh` is in PATH |
| `ISO not found` | Packer cannot locate the Ubuntu ISO | Verify `iso_url` / `iso_paths` variables; check datastore paths for VMware |
| `API auth failed` (NinjaOne) | Client ID/Secret incorrect or tenant mismatch | Verify `NINJA_CLIENT_ID` and `NINJA_CLIENT_SECRET`; confirm region in profile |
| `VM name already exists` | Target VM name conflicts with an existing VM | Choose a unique VM name or delete the existing VM first |
| `Hyper-V feature not enabled` | Hyper-V role is not installed on the Windows host | Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All |
| `Virtual switch not found` | Specified Hyper-V switch does not exist | Check `Get-VMSwitch`; update profile or prompt input |
| NetBird status shows `Disconnected` after boot | Setup key invalid or management URL unreachable | Verify the setup key in NetBird console; check firewall rules for UDP 33073/443 |
| NinjaOne agent not reporting | Installer URL expired or org/location mismatch | Re-run deployment; confirm org and location names exactly match NinjaOne portal |

### General debugging

- Use `--dry-run` to inspect the rendered autoinstall YAML and setup scripts without deploying.
- Check `deploy/vm_deploy.py` exit codes: `0` = success, `1` = deployment error, `2` = validation error.
- Review PowerCLI/Hyper-V script output in the console; the Python wrappers forward stdout/stderr.

---

## Project Layout

```
├── README.md
├── requirements.txt
├── config.yaml                     # Global defaults (ninjaone base_url, default_region, dns, username)
├── validate.py                     # Pre-flight validation script
├── packer/
│   ├── plugins.pkr.hcl
│   ├── variables.pkr.hcl
│   ├── build-vmware.pkr.hcl
│   ├── build-hyperv.pkr.hcl
│   ├── http/
│   │   ├── user-data
│   │   └── meta-data
│   └── scripts/
│       └── provision.sh
├── deploy/
│   ├── vm_deploy.py                # Main CLI entry point
│   ├── lib/
│   │   ├── __init__.py
│   │   ├── prompts.py              # 8-step interactive wizard
│   │   ├── config_builder.py       # Jinja2 renderer + password hashing
│   │   ├── ninjaone_client.py      # NinjaOne API v2 client (OAuth + org/location lookup)
│   │   ├── netbird_installer.py    # NetBird setup script generator
│   │   ├── vmware_deployer.py      # PowerCLI subprocess wrapper
│   │   └── hyperv_deployer.py      # Hyper-V PowerShell wrapper
│   └── templates/
│       ├── autoinstall.yaml.j2
│       ├── netbird-setup.sh.j2
│       └── ninjaone-setup.sh.j2
├── scripts/
│   ├── deploy-vmware.ps1
│   └── deploy-hyperv.ps1
├── examples/
│   ├── client-acme-corp.yaml
│   └── client-techstart.yaml
└── tests/
    ├── test_vm_deploy.py
    ├── test_config_builder.py
    ├── test_ninjaone_client.py
    ├── test_netbird_installer.py
    └── test_templates_render.py
```

---

## License

MIT
