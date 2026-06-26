variable "environment" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "db_secret_arn" {
  type        = string
  description = "ARN of Module 4's Secrets Manager entry for the shared DB credentials"
}
