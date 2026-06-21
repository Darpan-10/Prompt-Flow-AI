variable "environment" {
  type        = string
  description = "Environment name"
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Private subnet IDs"
}

variable "security_group_id" {
  type        = string
  description = "Security group allowing access from Module 4 services"
}

variable "node_type" {
  type        = string
  default     = "cache.t4g.micro"
  description = "ElastiCache node type"
}

variable "redis_auth_token" {
  type        = string
  sensitive   = true
  description = "Redis AUTH token (min 16 chars)"
}
