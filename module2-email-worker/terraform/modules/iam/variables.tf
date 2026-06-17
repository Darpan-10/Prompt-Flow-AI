variable "environment" {

  type = string

}


variable "s3_ingestion_bucket_arn" {

  type = string

}


variable "s3_quarantine_bucket_arn" {

  type = string

}


variable "secrets_arns" {

  type = list(string)

}



