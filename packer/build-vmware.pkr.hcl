source "vsphere-iso" "ubuntu" {
  vcenter_server      = var.vcenter_server
  username            = var.username
  password            = var.password
  datacenter          = var.datacenter
  cluster             = var.cluster
  datastore           = var.datastore
  folder              = "Templates"
  vm_name             = "ubuntu-24-04-template"
  guest_os_type       = "ubuntu64Guest"
  firmware            = "efi"
  CPUs                = 2
  RAM                 = 4096
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
  cd_files = ["${path.root}/http/user-data", "${path.root}/http/meta-data"]
  cd_label = "cidata"
  boot_command = [
    "c<wait>",
    "linux /casper/vmlinuz --- autoinstall ds=nocloud;s=/cdrom/ <enter><wait>",
    "initrd /casper/initrd <enter><wait>",
    "boot <enter>"
  ]
  boot_wait            = "5s"
  ssh_username         = "packer"
  ssh_password         = "packer"
  ssh_timeout          = "60m"
  shutdown_command     = "echo 'packer' | sudo -S shutdown -P now"
  convert_to_template  = true
}

build {
  sources = ["source.vsphere-iso.ubuntu"]

  provisioner "shell" {
    execute_command = "echo 'packer' | {{ .Vars }} sudo -S -E bash '{{ .Path }}'"
    script          = "${path.root}/scripts/provision.sh"
  }
}
