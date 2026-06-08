[CmdletBinding()]
param (
    [Parameter(Mandatory = $true)]
    [string]$VMName,

    [Parameter(Mandatory = $true)]
    [string]$TemplateVhdx,

    [Parameter(Mandatory = $true)]
    [string]$VMSwitch,

    [string]$VMPath = "C:\VMs",
    [int]$CPU = 2,
    [long]$MemoryBytes = 4GB,
    [long]$MinMemoryBytes = 1GB,
    [long]$MaxMemoryBytes = 8GB,
    [string]$ConfigIsoPath = ""
)

try {
    # Validate template VHDX exists
    if (-not (Test-Path -Path $TemplateVhdx)) {
        throw "Template VHDX not found: $TemplateVhdx"
    }

    # Create VM directory
    $vmDir = Join-Path -Path $VMPath -ChildPath $VMName
    if (-not (Test-Path -Path $vmDir)) {
        New-Item -ItemType Directory -Path $vmDir -Force -ErrorAction Stop | Out-Null
        Write-Host "Created VM directory: $vmDir"
    } else {
        Write-Warning "VM directory already exists: $vmDir"
    }

    # Create differencing VHD from template
    $vhdPath = Join-Path -Path $vmDir -ChildPath "$VMName.vhdx"
    New-VHD -Path $vhdPath -ParentPath $TemplateVhdx -Differencing -ErrorAction Stop | Out-Null
    Write-Host "Created differencing disk: $vhdPath"

    # Create Generation 2 VM
    $vm = New-VM -Name $VMName -Generation 2 -MemoryStartupBytes $MemoryBytes -VHDPath $vhdPath -SwitchName $VMSwitch -Path $VMPath -ErrorAction Stop
    Write-Host "Created Gen 2 VM '$VMName'."

    # Set processor count
    Set-VMProcessor -VMName $VMName -Count $CPU -ErrorAction Stop
    Write-Host "Set CPU count=$CPU."

    # Configure dynamic memory
    Set-VMMemory -VMName $VMName -DynamicMemoryEnabled $true -StartupBytes $MemoryBytes -MinimumBytes $MinMemoryBytes -MaximumBytes $MaxMemoryBytes -ErrorAction Stop
    Write-Host "Set DynamicMemory: Startup=$MemoryBytes, Min=$MinMemoryBytes, Max=$MaxMemoryBytes."

    # Attach config ISO if provided
    if ($ConfigIsoPath) {
        if (-not (Test-Path -Path $ConfigIsoPath)) {
            throw "Config ISO path not found: $ConfigIsoPath"
        }
        Add-VMDvdDrive -VMName $VMName -Path $ConfigIsoPath -ErrorAction Stop | Out-Null
        Write-Host "Attached config ISO '$ConfigIsoPath'."
    }

    # Enable Guest Service Interface
    Enable-VMIntegrationService -Name "Guest Service Interface" -VMName $VMName -ErrorAction Stop
    Write-Host "Enabled Guest Service Interface."

    # Start VM
    Start-VM -Name $VMName -ErrorAction Stop
    Write-Host "VM '$VMName' started successfully."
}
catch {
    Write-Error "Deployment failed for VM '$VMName': $_"
    exit 1
}
