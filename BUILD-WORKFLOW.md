# VM-Deploy-Tool Build Orchestration Workflow

> **Repo**: `https://github.com/GodSpoon/netbird-vm-gen`  
> **Plan Source**: `VM-Deployment-Plan.md`  
> **Agent Strategy**: Fast parallel subagents (`gemini flash` for boilerplate/generation, `pi/minimax-m2.7-highspeed` for logic/architecture)

---

## 1. Plan Analysis & Decomposition

### 1.1 Architecture Summary
The tool is a **two-stage system**:
- **Stage 1 (Build-Time)**: Packer creates golden Ubuntu 24.04 templates for VMware vSphere and Hyper-V.
- **Stage 2 (Runtime)**: A Python CLI (`vm_deploy.py`) interactively prompts technicians, fetches the NinjaOne agent via API v2, renders client-specific cloud-init configs, and deploys VMs via PowerCLI/PowerShell.

### 1.2 Component Dependency Graph

```
Foundation (dirs, requirements, config.yaml)
    ├── Packer Layer ──────┬──> build-vmware.pkr.hcl
    │                       └──> build-hyperv.pkr.hcl
    │                       └──> http/ user-data + meta-data
    │                       └──> scripts/ provision.sh
    ├── Template Layer ────┬──> autoinstall.yaml.j2
    │                       ├──> netbird-setup.sh.j2
    │                       └──> ninjaone-setup.sh.j2
    ├── Python Lib Layer ──┬──> prompts.py (interactive UI)
    │                       ├──> config_builder.py (Jinja2 renderer)
    │                       ├──> ninjaone_client.py (OAuth v2 API)
    │                       ├──> netbird_installer.py (script gen)
    │                       ├──> vmware_deployer.py (PowerCLI wrapper)
    │                       └──> hyperv_deployer.py (PS wrapper)
    ├── PowerShell Layer ──┬──> deploy-vmware.ps1
    │                       └──> deploy-hyperv.ps1
    └── Main Entry Point ──> vm_deploy.py (argparse + orchestration)
```

**Key insight**: The three layers — Packer, Templates, Python Lib, PowerShell — are **almost entirely independent** once the Foundation is laid. They can be built in parallel by separate subagents.

### 1.3 Subagent Assignment Matrix

| Component | Complexity | Best Agent | Rationale |
|-----------|-----------|------------|-----------|
| Foundation scaffolding | Low | Direct / gemini flash | Simple file creation, no logic |
| Packer HCL configs | Medium | gemini flash | Structured config, well-documented syntax |
| Jinja2 templates | Medium | gemini flash | Template rendering, shell-script generation |
| PowerShell deploy scripts | Medium | gemini flash | Straightforward hypervisor cmdlet sequences |
| `prompts.py` + `config_builder.py` | Medium-High | pi/minimax-m2.7-highspeed | Interactive state machine, validation logic |
| `ninjaone_client.py` | High | pi/minimax-m2.7-highspeed | OAuth flow, error handling, API abstraction |
| `netbird_installer.py` | Low-Medium | gemini flash | Thin wrapper around script generation |
| `vmware_deployer.py` + `hyperv_deployer.py` | Medium | gemini flash | PowerCLI/PS subprocess wrappers |
| `vm_deploy.py` entry point | High | pi/minimax-m2.7-highspeed | Main orchestration, CLI design, error handling |
| README + examples | Low | gemini flash | Documentation, YAML examples |

---

## 2. Execution Phases

### Phase 1: Foundation (Sequential)
**Owner**: Direct execution (no subagent needed — too small to delegate)

- Create directory tree: `packer/`, `deploy/`, `deploy/lib/`, `deploy/templates/`, `scripts/`, `examples/`
- Write `requirements.txt` with pinned deps (`rich`, `jinja2`, `requests`, `pyyaml`, `questionary`)
- Write `config.yaml` with global defaults (regions, URLs, default hardware)
- Write `.gitignore`

### Phase 2: Core Components (Parallel — 6 subagents)
**Strategy**: Spawn 6 subagents simultaneously. Each writes into its assigned directory.

#### Subagent 2A — Packer Layer (`gemini flash`)
**Files**: `packer/plugins.pkr.hcl`, `packer/variables.pkr.hcl`, `packer/build-vmware.pkr.hcl`, `packer/build-hyperv.pkr.hcl`, `packer/http/user-data`, `packer/http/meta-data`, `packer/scripts/provision.sh`

**Instructions**: Build Packer configs per the plan (Sections 3.3–3.5). Use `cd_files` with `cidata` label. Include open-vm-tools, hyperv-daemons, qemu-guest-agent. Clean machine-id in late-commands.

#### Subagent 2B — Jinja2 Templates (`gemini flash`)
**Files**: `deploy/templates/autoinstall.yaml.j2`, `deploy/templates/netbird-setup.sh.j2`, `deploy/templates/ninjaone-setup.sh.j2`

**Instructions**: Render full Jinja2 templates matching Section 12.1. The autoinstall template must support DHCP/static IP, SSH keys, password hashing, and late-commands that inject NetBird + NinjaOne scripts.

#### Subagent 2C — PowerShell Deploy Scripts (`gemini flash`)
**Files**: `scripts/deploy-vmware.ps1`, `scripts/deploy-hyperv.ps1`

**Instructions**: VMware script clones from template, sets CPU/RAM/network, optionally mounts cidata ISO. Hyper-V script creates differencing disk from template VHDX, sets dynamic memory, mounts ISO. Both output success + IP.

#### Subagent 2D — Python Prompts & Config Builder (`pi/minimax-m2.7-highspeed`)
**Files**: `deploy/lib/prompts.py`, `deploy/lib/config_builder.py`

**Instructions**: 
- `prompts.py`: Use `rich` + `questionary`. Implement 8-step wizard (hypervisor, VM details, hardware, network, credentials, NetBird, NinjaOne, review). Support `--profile` YAML pre-fill. Include `INSTALLERS` registry dict for extensibility.
- `config_builder.py`: Jinja2 environment setup, `render_autoinstall(vm_config) -> str`, `hash_password(plain) -> str` using `crypt`.

#### Subagent 2E — NinjaOne API Client (`pi/minimax-m2.7-highspeed`)
**File**: `deploy/lib/ninjaone_client.py`

**Instructions**: Full `NinjaOneClient` class with OAuth 2.0 client-credentials flow, token caching (in-memory), `get_organizations()`, `get_locations(org_id)`, `get_installer_url(org_id, location_id, installer_type='LINUX_DEB')`. Handle 401 re-auth, raise typed exceptions. Region map per Section 7.1.

#### Subagent 2F — Deployer Modules (`gemini flash`)
**Files**: `deploy/lib/netbird_installer.py`, `deploy/lib/vmware_deployer.py`, `deploy/lib/hyperv_deployer.py`

**Instructions**: 
- `netbird_installer.py`: Generate NetBird setup script from template + config dict.
- `vmware_deployer.py`: Python wrapper that calls `scripts/deploy-vmware.ps1` via subprocess, passing parameters. Validate pre-reqs (PowerCLI module, vCenter connectivity).
- `hyperv_deployer.py`: Same pattern for Hyper-V. Check Hyper-V role, call `scripts/deploy-hyperv.ps1`.

### Phase 3: Main Entry Point (Sequential — 1 subagent)
**Owner**: `pi/minimax-m2.7-highspeed`
**File**: `deploy/vm_deploy.py`

**Instructions**: 
- `argparse` with both interactive and non-interactive modes.
- Import and wire all `deploy/lib/` modules.
- Orchestration flow: parse args → load profile → run prompts (if interactive) → authenticate NinjaOne → fetch orgs/locations → render autoinstall → create cidata ISO → call deployer → verify.
- Add `--dry-run` flag.
- Exit codes: 0 = success, 1 = deployment error, 2 = validation error.

### Phase 4: Documentation & Examples (Parallel — 2 subagents)

#### Subagent 4A — README (`gemini flash`)
**File**: `README.md`

**Instructions**: Full technician guide: install, setup env vars, interactive walkthrough, non-interactive examples, profile usage, Packer template build instructions, troubleshooting.

#### Subagent 4B — Example Configs (`gemini flash`)
**Files**: `examples/client-acme-corp.yaml`, `examples/client-techstart.yaml`

**Instructions**: Valid YAML profiles showing all fields. Use `${ENV_VAR}` syntax for secrets.

### Phase 5: Integration Verification (Sequential)
**Owner**: Direct execution

1. `python -m py_compile deploy/*.py deploy/lib/*.py`
2. `python -c "import deploy.lib.prompts, deploy.lib.config_builder, deploy.lib.ninjaone_client"`
3. `packer fmt packer/*.pkr.hcl`
4. `git add -A && git commit -m "feat: implement VM deploy tool (MVP)" && git push origin main`

---

## 3. Parallelization Rules

- **Phase 2** is the widest parallel band. All 6 subagents can run simultaneously because they write to disjoint file sets.
- **Phase 3** must wait for Phase 2 because `vm_deploy.py` imports from `deploy/lib/`.
- **Phase 4** can start immediately after Phase 2 finishes (README references file names but doesn't import them).
- **Phase 5** is always last.

---

## 4. Quality Gates

### Per-Subagent Acceptance Criteria

| Subagent | Gate |
|----------|------|
| 2A (Packer) | `packer fmt` succeeds; `packer validate` passes after `packer init` |
| 2B (Templates) | Jinja2 syntax valid; renders without error against test dict |
| 2C (PS) | PowerShell parser accepts scripts (`powershell -Command "Get-Command"` dry-run) |
| 2D (Prompts/Builder) | `py_compile` passes; `config_builder.render_autoinstall()` returns string containing `#cloud-config` |
| 2E (NinjaOne) | `py_compile` passes; class has all 4 required methods |
| 2F (Deployers) | `py_compile` passes; modules import cleanly |
| 3 (Main) | `py_compile` passes; `--help` returns 0 |

### Global Invariants
- No hardcoded secrets.
- All credentials use env-var resolution or prompt-at-runtime.
- Passwords are hashed before embedding.
- Templates are parameterized — no client-specific defaults in Jinja2.

---

## 5. Re-execution Notes

If a subagent fails or produces subpar output:
1. Read the artifact from the failed subagent.
2. Re-spawn the **same** subagent with the failed file content in context and specific correction instructions.
3. Do not restart unrelated subagents.

If the dependency graph changes (e.g., new agent added):
1. Add entry to `prompts.py::INSTALLERS`.
2. Create `deploy/templates/<agent>-setup.sh.j2`.
3. Re-run Subagent 2B (Templates) + 2D (Prompts) only.

---

## 6. File Inventory (Expected Final State)

```
vm-deploy-tool/
├── README.md
├── requirements.txt
├── config.yaml
├── .gitignore
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
│   ├── vm_deploy.py
│   ├── lib/
│   │   ├── __init__.py
│   │   ├── prompts.py
│   │   ├── config_builder.py
│   │   ├── ninjaone_client.py
│   │   ├── netbird_installer.py
│   │   ├── vmware_deployer.py
│   │   └── hyperv_deployer.py
│   └── templates/
│       ├── autoinstall.yaml.j2
│       ├── netbird-setup.sh.j2
│       └── ninjaone-setup.sh.j2
├── scripts/
│   ├── deploy-vmware.ps1
│   └── deploy-hyperv.ps1
└── examples/
    ├── client-acme-corp.yaml
    └── client-techstart.yaml
```
