variable "vcenter_server" {
  type        = string
  description = "vCenter server hostname or IP address"
}

variable "username" {
  type        = string
  description = "vCenter or Hyper-V username"
}

variable "password" {
  type        = string
  description = "vCenter or Hyper-V password"
  sensitive   = true
}

variable "datacenter" {
  type        = string
  description = "vSphere datacenter name"
  default     = ""
}

variable "cluster" {
  type        = string
  description = "vSphere cluster name"
  default     = ""
}

variable "datastore" {
  type        = string
  description = "vSphere datastore for VM disks"
  default     = ""
}

variable "network" {
  type        = string
  description = "vSphere port group or network label"
  default     = ""
}

variable "iso_datastore" {
  type        = string
  description = "vSphere datastore containing the ISO"
  default     = ""
}

variable "iso_path" {
  type        = string
  description = "Path to the Ubuntu ISO on vSphere datastore"
  default     = ""
}

variable "iso_url" {
  type        = string
  description = "URL to download the Ubuntu ISO (fallback)"
  default     = ""
}

variable "iso_checksum" {
  type        = string
  description = "Checksum of the Ubuntu ISO"
  default     = ""
}

variable "hyperv_switch" {
  type        = string
  description = "Hyper-V virtual switch name"
  default     = ""
}
