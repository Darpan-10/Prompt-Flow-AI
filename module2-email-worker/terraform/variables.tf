variable "aws_region" {

  description = "AWS region — Mumbai (closest to SRM AP)"
  type        = string
  default     = "ap-south-1"

}


variable "environment" {

  description = "Deployment environment"
  type        = string
  default     = "dev"
  validation {

    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod"

  }


}


variable "db_password" {

  description = "RDS PostgreSQL master password — stored in Secrets Manager"
  type        = string
  sensitive   = true

}


variable "redis_auth_token" {

  description = "ElastiCache Redis auth token — stored in Secrets Manager"
  type        = string
  sensitive   = true

}


variable "imap_password" {

  description = "IMAP/Gmail App Password — stored in Secrets Manager"
  type        = string
  sensitive   = true

}

