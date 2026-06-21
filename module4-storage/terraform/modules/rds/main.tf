# terraform/modules/rds/main.tf
# Module 4: AWS RDS PostgreSQL 15 with pgvector support

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ── DB Subnet Group ───────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "promptflow" {
  name       = "promptflow-db-subnet-${var.environment}"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name        = "promptflow-db-subnet-${var.environment}"
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

# ── DB Parameter Group (enables pgvector via shared_preload_libraries) ──────

resource "aws_db_parameter_group" "promptflow_pg15" {
  name   = "promptflow-pg15-${var.environment}"
  family = "postgres15"

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
  }

  parameter {
    name  = "log_statement"
    value = "ddl"
  }

  parameter {
    name         = "log_min_duration_statement"
    value        = "1000"
    apply_method = "immediate"
  }

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

# ── RDS Instance ───────────────────────────────────────────────────────────────

resource "aws_db_instance" "promptflow" {
  identifier     = "promptflow-${var.environment}"
  engine         = "postgres"
  engine_version = "15.7"

  instance_class        = var.db_instance_class
  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "promptflow"
  username = "promptflow"
  password = var.db_password
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.promptflow.name
  vpc_security_group_ids = [var.security_group_id]
  parameter_group_name   = aws_db_parameter_group.promptflow_pg15.name

  multi_az            = var.environment == "prod" ? true : false
  publicly_accessible = false

  backup_retention_period = var.environment == "prod" ? 30 : 7
  backup_window            = "03:00-04:00"
  maintenance_window       = "mon:04:00-mon:05:00"

  deletion_protection      = var.environment == "prod" ? true : false
  skip_final_snapshot      = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "promptflow-final-${formatdate("YYYY-MM-DD", timestamp())}" : null

  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  copy_tags_to_snapshot = true

  tags = {
    Name        = "promptflow-${var.environment}"
    Environment = var.environment
    Module      = "Module4_Storage"
    Compliance  = "NAAC-7yr-retention"
  }
}

# ── Secrets Manager (DB credentials) ──────────────────────────────────────────

resource "aws_secretsmanager_secret" "db_credentials" {
  name        = "/promptflow/${var.environment}/db-credentials"
  description = "Module 4 PostgreSQL credentials"

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

resource "aws_secretsmanager_secret_version" "db_credentials" {
  secret_id = aws_secretsmanager_secret.db_credentials.id
  secret_string = jsonencode({
    username = aws_db_instance.promptflow.username
    password = var.db_password
    host     = aws_db_instance.promptflow.address
    port     = aws_db_instance.promptflow.port
    dbname   = aws_db_instance.promptflow.db_name
    url      = "postgresql+asyncpg://${aws_db_instance.promptflow.username}:${var.db_password}@${aws_db_instance.promptflow.address}:${aws_db_instance.promptflow.port}/${aws_db_instance.promptflow.db_name}"
  })
}

# ── Outputs ────────────────────────────────────────────────────────────────────

output "rds_endpoint" {
  value       = aws_db_instance.promptflow.address
  description = "RDS instance endpoint (hostname only)"
}

output "rds_port" {
  value       = aws_db_instance.promptflow.port
  description = "RDS instance port"
}

output "rds_arn" {
  value       = aws_db_instance.promptflow.arn
  description = "RDS instance ARN"
}

output "db_secret_arn" {
  value       = aws_secretsmanager_secret.db_credentials.arn
  description = "Secrets Manager ARN for DB credentials"
}

output "database_url" {
  value       = "postgresql+asyncpg://${aws_db_instance.promptflow.username}:${var.db_password}@${aws_db_instance.promptflow.address}:${aws_db_instance.promptflow.port}/${aws_db_instance.promptflow.db_name}"
  sensitive   = true
  description = "Full async database URL"
}
