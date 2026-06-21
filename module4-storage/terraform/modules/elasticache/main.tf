# terraform/modules/elasticache/main.tf
# Module 4: AWS ElastiCache Redis for idempotency caching

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_elasticache_subnet_group" "promptflow" {
  name       = "promptflow-redis-subnet-${var.environment}"
  subnet_ids = var.private_subnet_ids
}

resource "aws_elasticache_parameter_group" "promptflow" {
  name   = "promptflow-redis7-${var.environment}"
  family = "redis7"

  parameter {
    name  = "maxmemory-policy"
    value = "volatile-lru"
  }
}

resource "aws_elasticache_replication_group" "promptflow" {
  replication_group_id = "promptflow-${var.environment}"
  description           = "Module 4 idempotency cache"

  engine         = "redis"
  engine_version = "7.0"
  node_type      = var.node_type

  num_cache_clusters = var.environment == "prod" ? 2 : 1
  automatic_failover_enabled = var.environment == "prod"

  port = 6379

  subnet_group_name = aws_elasticache_subnet_group.promptflow.name
  security_group_ids = [var.security_group_id]
  parameter_group_name = aws_elasticache_parameter_group.promptflow.name

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                  = var.redis_auth_token

  auto_minor_version_upgrade = true

  snapshot_retention_limit = var.environment == "prod" ? 7 : 1
  snapshot_window           = "03:00-05:00"
  maintenance_window        = "mon:05:00-mon:06:00"

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

output "redis_endpoint" {
  value       = aws_elasticache_replication_group.promptflow.primary_endpoint_address
  description = "Redis primary endpoint"
}

output "redis_port" {
  value       = 6379
  description = "Redis port"
}

output "redis_url" {
  value       = "rediss://:${var.redis_auth_token}@${aws_elasticache_replication_group.promptflow.primary_endpoint_address}:6379"
  sensitive   = true
  description = "Full Redis URL with TLS"
}
