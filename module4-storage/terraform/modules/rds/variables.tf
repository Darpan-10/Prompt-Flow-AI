variable "environment" {
  type        = string
  description = "Environment name (dev, staging, prod)"
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Private subnet IDs for RDS subnet group"
}

variable "security_group_id" {
  type        = string
  description = "Security group ID allowing access from Module 3/4 services"
}

variable "db_instance_class" {
  type        = string
  default     = "db.t4g.small"
  description = "RDS instance class"
}

variable "allocated_storage" {
  type        = number
  default     = 50
  description = "Initial allocated storage in GB"
}

variable "max_allocated_storage" {
  type        = number
  default     = 200
  description = "Max storage for autoscaling in GB"
}

variable "db_password" {
  type        = string
  sensitive   = true
  description = "Master password for RDS instance"
}
