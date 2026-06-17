output "vpc_id" {

  description = "VPC ID"
  value       = module.vpc.vpc_id

}


output "private_subnet_ids" {

  description = "Private subnet IDs"
  value       = module.vpc.private_subnet_ids

}


output "rds_endpoint" {

  description = "RDS PostgreSQL endpoint"
  value       = module.rds.endpoint
  sensitive   = true

}


output "redis_endpoint" {

  description = "ElastiCache Redis endpoint"
  value       = module.elasticache.primary_endpoint
  sensitive   = true

}



output "s3_ingestion_bucket" {

  description = "S3 ingestion bucket name"
  value       = module.s3.ingestion_bucket_name

}


output "s3_quarantine_bucket" {

  description = "S3 quarantine bucket name"
  value       = module.s3.quarantine_bucket_name

}


output "worker_iam_role_arn" {

  description = "IAM role ARN for Module 2 worker"
  value       = module.iam.worker_role_arn

}


output "cognito_user_pool_id" {

  description = "Cognito User Pool ID"
  value       = module.cognito.user_pool_id

}


output "cognito_m2m_client_id" {

  description = "Cognito M2M App Client ID"
  value       = module.cognito.m2m_client_id
  sensitive   = true

}

