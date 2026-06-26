# terraform/modules/security_groups/main.tf
# Module 4: Security groups for RDS + ElastiCache + ECS service

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ── Security Group: Module 4 ECS Service ──────────────────────────────────────

# NOTE on checkov finding CKV2_AWS_5 ("Security Group attached to another
# resource") for this SG: genuinely not yet attached -- the ECS module
# that would reference it (modules/ecs) is intentionally commented out in
# root main.tf pending ECR repo URL / ALB ARNs / MSK broker info. This is
# a REAL gap, not a false positive -- it will resolve once modules/ecs is
# uncommented and wired to service_security_group_id =
# module.security_groups.module4_service_sg_id. (Inline #checkov:skip
# does not work for this specific check -- CKV2_AWS_5 is a graph-based
# check with a confirmed upstream checkov bug where inline suppression of
# "is this resource attached" checks is silently ignored; see
# TERRAFORM_TESTING.md for the tracking issue link and how this is
# instead documented/tracked.)
resource "aws_security_group" "module4_service" {
  name        = "module4-storage-service-${var.environment}"
  description = "Module 4 Kafka consumer + FastAPI service"
  vpc_id      = var.vpc_id

  # CKV_AWS_382: egress scoped to the specific ports this service actually
  # needs, rather than -1 (all ports). Destination remains 0.0.0.0/0 for
  # the HTTPS/Kafka rules because AWS service endpoints (S3, Secrets
  # Manager, Bedrock, ECR, CloudWatch Logs, MSK brokers) don't have fixed
  # IPs reachable via a narrower CIDR without provisioning VPC endpoints.
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS to AWS APIs (S3, Secrets Manager, Bedrock, ECR, CloudWatch Logs)"
  }

  egress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "PostgreSQL to RDS within VPC"
  }

  egress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Redis to ElastiCache within VPC"
  }

  egress {
    from_port   = 9092
    to_port     = 9096
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Kafka/MSK broker ports within VPC (plaintext, TLS, SASL variants)"
  }

  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
    description = "DNS resolution within VPC"
  }

  ingress {
    from_port   = 8003
    to_port     = 8003
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "FastAPI from within VPC (ALB)"
  }

  tags = {
    Name        = "module4-storage-service-${var.environment}"
    Environment = var.environment
  }
}

# ── Security Group: RDS PostgreSQL ────────────────────────────────────────────

# NOTE on checkov finding CKV2_AWS_5 for this SG: it IS attached -- passed
# as security_group_id into the rds module's aws_db_instance resource
# (see root main.tf: module.rds { security_group_id =
# module.security_groups.rds_sg_id }). This is a FALSE POSITIVE -- checkov's
# graph builder does not reliably trace SG attachment across module
# boundaries. Inline #checkov:skip does not suppress this specific check
# (confirmed upstream bug, not a typo in this file) -- see
# TERRAFORM_TESTING.md for verification steps and the tracking issue.
resource "aws_security_group" "rds" {
  name        = "promptflow-rds-${var.environment}"
  description = "Allow PostgreSQL access from Module 3 + Module 4 services"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.module4_service.id]
    description     = "PostgreSQL from Module 4 service"
  }

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.module3_security_group_ids
    description     = "PostgreSQL from Module 3 (extraction worker, if needed)"
  }

  # CKV_AWS_382/CKV_AWS_23: RDS itself never needs to initiate outbound
  # connections to the internet under normal operation -- snapshots,
  # backups, and replication are managed internally by AWS outside the
  # security group's egress path. Egress is restricted to within the VPC
  # only (not removed entirely, in case a future feature like cross-AZ
  # internal replication traffic needs it).
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
    description = "Restrict outbound to within the VPC only (RDS does not need internet egress)"
  }

  tags = {
    Name        = "promptflow-rds-${var.environment}"
    Environment = var.environment
  }
}

# ── Security Group: ElastiCache Redis ─────────────────────────────────────────

# NOTE on checkov finding CKV2_AWS_5 for this SG: it IS attached -- passed
# as security_group_id into the elasticache module's
# aws_elasticache_replication_group resource (see root main.tf). Same
# false-positive / inline-skip-doesn't-work situation as the rds SG above.
resource "aws_security_group" "redis" {
  name        = "promptflow-redis-${var.environment}"
  description = "Allow Redis access from Module 4 service only"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.module4_service.id]
    description     = "Redis from Module 4 service"
  }

  # Same rationale as the rds security group: ElastiCache does not need
  # internet egress for normal operation -- restrict to within the VPC.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
    description = "Restrict outbound to within the VPC only (ElastiCache does not need internet egress)"
  }

  tags = {
    Name        = "promptflow-redis-${var.environment}"
    Environment = var.environment
  }
}

output "module4_service_sg_id" {
  value = aws_security_group.module4_service.id
}

output "rds_sg_id" {
  value = aws_security_group.rds.id
}

output "redis_sg_id" {
  value = aws_security_group.redis.id
}
