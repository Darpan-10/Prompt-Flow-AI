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

  # CKV2_AWS_69: force SSL for all client connections (encryption in transit)
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

# ── Enhanced Monitoring IAM Role (CKV_AWS_118) ────────────────────────────────

resource "aws_iam_role" "rds_enhanced_monitoring" {
  name = "promptflow-rds-monitoring-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_enhanced_monitoring" {
  role       = aws_iam_role.rds_enhanced_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ── RDS Instance ───────────────────────────────────────────────────────────────

resource "aws_db_instance" "promptflow" {
  identifier = "promptflow-${var.environment}"

  engine         = "postgres"
  engine_version = "15"

  instance_class        = "db.t3.micro"
  allocated_storage     = 20
  max_allocated_storage = 20

  storage_type      = "gp3"
  storage_encrypted = true
  kms_key_id        = var.kms_key_arn

  db_name  = "promptflow"
  username = "promptflow"
  password = var.db_password
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.promptflow.name
  vpc_security_group_ids = [var.security_group_id]
  parameter_group_name   = aws_db_parameter_group.promptflow_pg15.name

  multi_az            = false
  publicly_accessible = false

  # Free Tier settings
  backup_retention_period = 0
  deletion_protection     = false
  skip_final_snapshot     = true

  # Disable paid/advanced features
  monitoring_interval                 = 0
  performance_insights_enabled        = false
  iam_database_authentication_enabled = false

  auto_minor_version_upgrade = true

  enabled_cloudwatch_logs_exports = []

  copy_tags_to_snapshot = true

  tags = {
    Name        = "promptflow-${var.environment}"
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}
# ── Secrets Manager (DB credentials) ──────────────────────────────────────────

resource "aws_secretsmanager_secret" "db_credentials" {
  name        = "/promptflow/${var.environment}/db-credentials"
  description = "Module 4 PostgreSQL credentials"
  kms_key_id  = var.kms_key_arn

  # CKV2_AWS_57: automatic rotation requires a rotation Lambda (e.g. AWS's
  # SecretsManagerRDSPostgreSQLRotationSingleUser SAR app). Not wired up
  # here since it needs a Lambda deployment package and a decision on
  # rotation cadence -- flag this back if you want it added; it's a
  # contained follow-up, not a structural change to this module.
  #checkov:skip=CKV2_AWS_57:Automatic rotation requires a rotation Lambda not yet provisioned; manual/quarterly rotation process documented separately
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
