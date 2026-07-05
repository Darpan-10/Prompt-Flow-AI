variable "environment" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "module4_rds_sg_id" {
  type        = string
  description = "Security group ID of Module 4's RDS instance; Module 6 adds an ingress rule to it"
}
