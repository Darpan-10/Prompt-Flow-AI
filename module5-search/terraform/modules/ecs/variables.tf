variable "environment" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "service_security_group_id" {
  type        = string
  description = "Security group for the search-api task (module5_service from the security_groups module)"
}

variable "ecs_execution_role_arn" {
  type = string
}

variable "ecs_task_role_arn" {
  type = string
}

variable "ecr_repository_url" {
  type        = string
  description = "ECR repo URL, e.g. 123456789.dkr.ecr.ap-south-1.amazonaws.com/promptflow-module5"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "db_secret_arn" {
  type        = string
  description = "ARN of Module 4's Secrets Manager entry for the shared DB credentials (looked up via data source in root main.tf)"
}

variable "redis_url" {
  type        = string
  sensitive   = true
  description = "Module 4's full Redis connection URL (e.g. from `terraform output -raw redis_url` in Module 4's stack). Wrapped in Module 5's own Secrets Manager entry by this module -- see the comment above aws_secretsmanager_secret.redis_url in main.tf for why."
}

variable "jwt_public_key" {
  type        = string
  description = "Module 1's RS256 public key (PEM format) for verifying JWTs in production. Not secret material (public keys are meant to be public) but required for SKIP_JWT_VALIDATION=false to work."
}

variable "kms_key_arn" {
  type        = string
  description = "ARN of Module 4's shared customer-managed KMS key (looked up via data \"aws_kms_alias\" in root main.tf), used to encrypt the CloudWatch log group and the Redis URL secret wrapper"
}

variable "target_group_arn" {
  type        = string
  description = "ALB target group ARN for the search-api service"
}

variable "alb_listener_arn" {
  type        = string
  description = "ALB listener ARN -- service depends on this existing before attaching"
}

variable "task_cpu" {
  type        = number
  default     = 1024 # 1 vCPU -- locked sizing decision
  description = "Fargate task CPU units (1024 = 1 vCPU)"
}

variable "task_memory" {
  type        = number
  default     = 2048 # 2GB -- locked sizing decision (embedding model + PyTorch runtime headroom)
  description = "Fargate task memory in MB"
}

variable "desired_count" {
  type    = number
  default = 2 # min 2 for availability behind ALB
}

variable "max_count" {
  type    = number
  default = 6
}
