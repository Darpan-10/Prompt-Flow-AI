variable "environment" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "db_secret_arn" {
  type        = string
  description = "ARN of Module 4's DB credentials Secrets Manager entry"
}

variable "reports_bucket_arn" {
  type        = string
  description = "ARN of the S3 reports bucket (from modules/s3 output)"
}

variable "kms_key_arn" {
  type        = string
  description = "ARN of Module 4's shared CMK (for S3 PutObject with SSE-KMS)"
}
