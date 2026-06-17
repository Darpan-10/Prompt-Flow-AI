output "vpc_id" {
  value = module.vpc.vpc_id
}

output "rds_endpoint" {
  value     = module.rds.endpoint
  sensitive = true
}

output "redis_endpoint" {
  value     = module.elasticache.primary_endpoint
  sensitive = true
}


output "s3_ingestion_bucket" {
  value = module.s3.ingestion_bucket_name
}

output "worker_iam_role_arn" {
  value = module.iam.worker_role_arn
}

output "cognito_user_pool_id" {
  value = module.cognito.user_pool_id
}

