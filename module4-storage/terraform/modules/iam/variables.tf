variable "environment" {
  type = string
}

variable "aws_region" {
  type    = string
  default = "ap-south-1"
}

variable "db_secret_arn" {
  type = string
}

variable "redis_secret_arn" {
  type    = string
  default = ""
}

variable "s3_bucket_arn" {
  type = string
}
