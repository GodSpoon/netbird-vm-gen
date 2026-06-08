packer {
  required_plugins {
    vsphere = {
      version = ">= 1.2.0"
      source  = "github.com/hashicorp/vsphere"
    }
    hyperv = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/hyperv"
    }
  }
}
