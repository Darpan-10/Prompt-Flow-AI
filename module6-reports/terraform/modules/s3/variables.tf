variable "environment" {
  type = string
}

variable "kms_key_arn" {
  type        = string
  description = "ARN of Module 4's shared customer-managed KMS key (looked up via data source in root main.tf)"
}

variable "access_log_bucket_id" {
  type        = string
  default     = ""
  description = "ID of a SEPARATE, SSE-S3-encrypted bucket to receive access logs. Must NOT be this same bucket (it uses SSE-KMS, which AWS does not support as a logging destination). Leave blank to disable access logging entirely until a dedicated SSE-S3 logging bucket exists."
}
