variable "environment" {
  type = string
}

variable "vpc_id" {
  type        = string
  description = "VPC ID from networking module"
}

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR block, e.g. 10.0.0.0/16"
}

variable "module3_security_group_ids" {
  type        = list(string)
  default     = []
  description = "Security group IDs of Module 3 worker (for DB access if needed)"
}
