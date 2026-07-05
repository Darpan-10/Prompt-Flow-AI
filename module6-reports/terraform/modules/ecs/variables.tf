variable "environment" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "service_security_group_id" {
  type = string
}

variable "ecs_execution_role_arn" {
  type = string
}

variable "ecs_task_role_arn" {
  type = string
}

variable "ecr_repository_url" {
  type    = string
  default = ""
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "db_secret_arn" {
  type        = string
  description = "ARN of Module 4's DB credentials Secrets Manager entry"
}

variable "reports_bucket_name" {
  type        = string
  description = "Name (not ARN) of the reports S3 bucket"
}

variable "jwt_public_key" {
  type        = string
  default     = ""
  description = "Module 1's RS256 public key (PEM format)"
}

variable "kms_key_arn" {
  type        = string
  description = "ARN of Module 4's shared CMK for CloudWatch Logs encryption"
}

variable "target_group_arn" {
  type    = string
  default = ""
}

variable "alb_listener_arn" {
  type    = string
  default = ""
}

variable "task_cpu" {
  type    = number
  default = 512
  description = "Fargate task CPU (512 = 0.5 vCPU). No ML model to load; WeasyPrint is pure CPU but low-volume."
}

variable "task_memory" {
  type    = number
  default = 1024
  description = "Fargate task memory in MB (1GB -- no torch/sentence-transformers needed)"
}

variable "desired_count" {
  type    = number
  default = 2
}
