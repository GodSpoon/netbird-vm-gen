<#requires -Modules VMware.VimAutomation.Core#>
[CmdletBinding()]
param (
    [Parameter(Mandatory = $true)]
    [string]$VMName,

    [Parameter(Mandatory = $true)]
    [string]$TemplateName,

    [Parameter(Mandatory = $true)]
    [string]$VMHost,

    [Parameter(Mandatory = $true)]
    [string]$Datastore,

    [string]$Folder = "",
    [int]$CPU = 2,
    [int]$MemoryMB = 4096,
    [string]$NetworkName = "VM Network",
    [string]$CustomizationSpecName = "",
    [string]$ConfigIsoPath = ""
)

try {
    # Connect to vCenter
    if ($env:VCENTER_SERVER) {
        $server = $env:VCENTER_SERVER
    } else {
        throw "Environment variable VCENTER_SERVER is not set."
    }

    if ($env:VCENTER_USER -and $env:VCENTER_PASS) {
        $securePass = ConvertTo-SecureString $env:VCENTER_PASS -AsPlainText -Force
        $cred = New-Object System.Management.Automation.PSCredential ($env:VCENTER_USER, $securePass)
    } else {
        $cred = Get-Credential -Message "Enter vCenter credentials"
    }

    $viserver = Connect-VIServer -Server $server -Credential $cred -ErrorAction Stop
    Write-Host "Connected to vCenter: $server"

    # Retrieve template
    $template = Get-Template -Name $TemplateName -ErrorAction Stop
    if (-not $template) {
        throw "Template '$TemplateName' not found."
    }

    # Build New-VM parameters
    $newVmParams = @{
        Name       = $VMName
        Template   = $template
        VMHost     = $VMHost
        Datastore  = $Datastore
        ErrorAction = 'Stop'
    }

    if ($Folder) {
        $vmFolder = Get-Folder -Name $Folder -ErrorAction SilentlyContinue
        if ($vmFolder) {
            $newVmParams['Location'] = $vmFolder
        } else {
            Write-Warning "Folder '$Folder' not found; deploying to default location."
        }
    }

    # Deploy VM
    $vm = New-VM @newVmParams
    Write-Host "Deployed VM '$VMName' from template '$TemplateName'."

    # Configure CPU and Memory
    $vm = Set-VM -VM $vm -NumCpu $CPU -MemoryMB $MemoryMB -Confirm:$false -ErrorAction Stop
    Write-Host "Set CPU=$CPU, MemoryMB=$MemoryMB."

    # Configure network adapter
    $adapter = Get-NetworkAdapter -VM $vm -ErrorAction Stop
    if ($adapter) {
        $adapter | Set-NetworkAdapter -NetworkName $NetworkName -Confirm:$false -ErrorAction Stop | Out-Null
        Write-Host "Set network adapter to '$NetworkName'."
    }

    # Apply customization spec if provided
    if ($CustomizationSpecName) {
        $spec = Get-OSCustomizationSpec -Name $CustomizationSpecName -ErrorAction Stop
        if (-not $spec) {
            throw "OS Customization Spec '$CustomizationSpecName' not found."
        }
        Set-VM -VM $vm -OSCustomizationSpec $spec -Confirm:$false -ErrorAction Stop | Out-Null
        Write-Host "Applied OS Customization Spec '$CustomizationSpecName'."
    }

    # Attach config ISO if provided
    if ($ConfigIsoPath) {
        if (-not (Test-Path $ConfigIsoPath)) {
            throw "Config ISO path not found: $ConfigIsoPath"
        }
        New-CDDrive -VM $vm -IsoPath $ConfigIsoPath -StartConnected:$true -ErrorAction Stop | Out-Null
        Write-Host "Attached config ISO '$ConfigIsoPath' with StartConnected."
    }

    # Power on VM
    Start-VM -VM $vm -ErrorAction Stop | Out-Null
    Write-Host "VM '$VMName' started successfully."

    # Output IP address
    $ip = ($vm.Guest.IPAddress | Select-Object -First 1)
    if ($ip) {
        Write-Host "Guest IP Address: $ip"
    } else {
        Write-Host "Guest IP Address not yet available."
    }
}
catch {
    Write-Error "Deployment failed for VM '$VMName': $_"
    exit 1
}
finally {
    if ($viserver -and $viserver.IsConnected) {
        Disconnect-VIServer -Server $viserver -Confirm:$false -ErrorAction SilentlyContinue
        Write-Host "Disconnected from vCenter."
    }
}
