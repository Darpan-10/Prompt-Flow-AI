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

resource "aws_security_group" "module4_service" {
  name        = "module4-storage-service-${var.environment}"
  description = "Module 4 Kafka consumer + FastAPI service"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
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

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "promptflow-rds-${var.environment}"
    Environment = var.environment
  }
}

# ── Security Group: ElastiCache Redis ─────────────────────────────────────────

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

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
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
