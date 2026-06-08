source "hyperv-iso" "ubuntu" {
  vm_name          = "ubuntu-24-04-template"
  generation       = 2
  enable_secure_boot = true
  secure_boot_template = "MicrosoftUEFICertificateAuthority"
  cpus             = 2
  memory           = 4096
  disk_size        = 25600
  switch_name      = var.hyperv_switch
  iso_url          = var.iso_url
  iso_checksum     = var.iso_checksum
  cd_files         = ["${path.root}/http/user-data", "${path.root}/http/meta-data"]
  cd_label         = "cidata"
  boot_command = [
    "c<wait>",
    "linux /casper/vmlinuz --- autoinstall ds=nocloud;s=/cdrom/ <enter><wait>",
    "initrd /casper/initrd <enter><wait>",
    "boot <enter>"
  ]
  boot_wait        = "5s"
  ssh_username     = "packer"
  ssh_password     = "packer"
  ssh_timeout      = "60m"
  shutdown_command = "echo 'packer' | sudo -S shutdown -P now"
}

build {
  sources = ["source.hyperv-iso.ubuntu"]

  provisioner "shell" {
    execute_command = "echo 'packer' | {{ .Vars }} sudo -S -E bash '{{ .Path }}'"
    script          = "${path.root}/scripts/provision.sh"
  }
}
