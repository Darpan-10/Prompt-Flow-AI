variable "environment" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "service_security_group_id" {
  type        = string
  description = "Security group for both consumer + API tasks (module4_service from security_groups module)"
}

variable "ecs_execution_role_arn" {
  type = string
}

variable "ecs_task_role_arn" {
  type = string
}

variable "ecr_repository_url" {
  type        = string
  description = "ECR repo URL, e.g. 123456789.dkr.ecr.ap-south-1.amazonaws.com/promptflow-module4"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "db_secret_arn" {
  type        = string
  description = "Secrets Manager ARN containing DATABASE_URL (from rds module's db_secret_arn output)"
}

variable "redis_secret_arn" {
  type        = string
  description = "Secrets Manager ARN containing REDIS_URL"
}

variable "kafka_bootstrap_brokers" {
  type        = string
  description = "MSK bootstrap broker string from Module 3's Terraform output"
}

variable "target_group_arn" {
  type        = string
  description = "ALB target group ARN for the API service"
}

variable "alb_listener_arn" {
  type        = string
  description = "ALB listener ARN -- service depends on this existing before attaching"
}

# ── Sizing ─────────────────────────────────────────────────────────────────────
# Consumer needs more memory than typical due to sentence-transformers +
# torch (CPU) being loaded in-process (~600-900MB resident for the model
# plus PyTorch runtime overhead).

variable "consumer_cpu" {
  type    = number
  default = 1024  # 1 vCPU
}

variable "consumer_memory" {
  type    = number
  default = 2048  # 2GB -- accounts for sentence-transformers model in memory
}

variable "consumer_desired_count" {
  type    = number
  default = 1
}

variable "api_cpu" {
  type    = number
  default = 512  # 0.5 vCPU -- API doesn't load the embedding model
}

variable "api_memory" {
  type    = number
  default = 1024  # 1GB
}

variable "api_desired_count" {
  type    = number
  default = 2  # min 2 for availability behind ALB
}

variable "api_max_count" {
  type    = number
  default = 6
}
