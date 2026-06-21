variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

# NOTE: VPC name is now derived automatically as "promptflow-${var.environment}-vpc"
# to match the existing aws_vpc.main resource tag from Module 1-3's vpc.tf.
# No separate vpc_name variable needed -- see terraform/main.tf data "aws_vpc" "main".

variable "module3_security_group_ids" {
  type        = list(string)
  default     = []
  description = "Module 3 worker security group IDs (for DB access if needed)"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.small"
}

variable "allocated_storage" {
  type    = number
  default = 50
}

variable "max_allocated_storage" {
  type    = number
  default = 200
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "redis_auth_token" {
  type      = string
  sensitive = true
}

variable "s3_bucket_arn" {
  type        = string
  description = "ARN of promptflow-ingestion-dev S3 bucket (Module 2/3)"
}

# ── ECS Fargate variables (used once modules/ecs is uncommented) ─────────────

variable "ecr_repository_url" {
  type        = string
  default     = ""
  description = "ECR repository URL for the Module 4 Docker image"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "redis_secret_arn" {
  type        = string
  default     = ""
  description = "Secrets Manager ARN wrapping the Redis URL for ECS task injection"
}

variable "kafka_bootstrap_brokers" {
  type        = string
  default     = ""
  description = "MSK bootstrap broker string (from Module 3 terraform output kafka_bootstrap_brokers)"
}

variable "target_group_arn" {
  type        = string
  default     = ""
  description = "ALB target group ARN for the Module 4 API service"
}

variable "alb_listener_arn" {
  type        = string
  default     = ""
  description = "ALB listener ARN the API service depends on"
}
