variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

# ── ECS Fargate variables (used once modules/ecs is uncommented) ─────────────

variable "ecr_repository_url" {
  type        = string
  default     = ""
  description = "ECR repository URL for the Module 5 Docker image"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "redis_url" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Module 4's full Redis connection URL (get via `terraform output -raw redis_url` from Module 4's stack)"
}

variable "jwt_public_key" {
  type        = string
  default     = ""
  description = "Module 1's RS256 public key (PEM format), required once SKIP_JWT_VALIDATION=false"
}

variable "target_group_arn" {
  type        = string
  default     = ""
  description = "ALB target group ARN for the Module 5 search-api service"
}

variable "alb_listener_arn" {
  type        = string
  default     = ""
  description = "ALB listener ARN the search-api service depends on"
}
