variable "create_vpc" {
  type        = bool
  default     = false
  description = "Whether to create a new VPC. Set false to reuse an existing VPC via data lookup instead (recommended for Module 4, since the VPC already exists from Module 1-3)."
}

variable "environment" {
  type = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  type    = list(string)
  default = ["ap-south-1a", "ap-south-1b"]
}
