variable "environment" {
  type = string
}

variable "vpc_id" {
  type        = string
  description = "VPC ID (looked up from Module 4's existing VPC by tag)"
}

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR block"
}

variable "module4_rds_sg_id" {
  type        = string
  description = "Security group ID of Module 4's RDS instance (looked up by tag promptflow-rds-ENVIRONMENT); Module 5 adds an ingress rule to this SG"
}

variable "module4_redis_sg_id" {
  type        = string
  description = "Security group ID of Module 4's ElastiCache Redis (looked up by tag promptflow-redis-ENVIRONMENT); Module 5 adds an ingress rule to this SG"
}
