variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "access_log_bucket_id" {
  type        = string
  default     = ""
  description = "ID of a separate, SSE-S3-encrypted bucket for S3 access logs (leave blank to disable -- see modules/s3/variables.tf for why self-logging isn't an option)"
}

# ── ECS Fargate variables (used once modules/ecs + modules/iam are uncommented) ──

variable "ecr_repository_url" {
  type    = string
  default = ""
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "jwt_public_key" {
  type    = string
  default = ""
}

variable "target_group_arn" {
  type    = string
  default = ""
}

variable "alb_listener_arn" {
  type    = string
  default = ""
}
