# terraform/modules/security_groups/main.tf
# Module 6: Security Group for the reports-api ECS task + cross-stack
# ingress rules into Module 4's RDS.
# (No Redis ingress needed -- Module 6 has no caching layer.)

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# NOTE on checkov finding CKV2_AWS_5 ("Security Group attached to
# another resource") for this SG: genuinely not yet attached -- the ECS
# module that would reference it (modules/ecs) is intentionally
# commented out in root main.tf pending ECR repo URL / ALB ARNs / JWT
# public key inputs. This is a REAL gap, not a false positive -- it
# resolves once modules/ecs is uncommented and wired to
# service_security_group_id = module.security_groups.module6_service_sg_id.
# Same documented situation as Module 5's equivalent SG (see Module 5's
# TERRAFORM_TESTING.md for the confirmed-checkov-bug background on why
# inline #checkov:skip doesn't work for this specific check).
resource "aws_security_group" "module6_service" {
  name        = "module6-reports-service-${var.environment}"
  description = "Module 6 reports-api FastAPI + background task service"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 8006
    to_port     = 8006
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "FastAPI reports-api from within VPC (ALB)"
  }

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS to AWS APIs (S3, Secrets Manager, ECR, CloudWatch Logs)"
  }

  egress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "PostgreSQL to Module 4's RDS within VPC"
  }

  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
    description = "DNS resolution within VPC"
  }

  tags = {
    Name        = "module6-reports-service-${var.environment}"
    Environment = var.environment
    Module      = "Module6_Reports"
  }
}

resource "aws_security_group_rule" "module6_to_rds" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = var.module4_rds_sg_id
  source_security_group_id = aws_security_group.module6_service.id
  description              = "PostgreSQL from Module 6 reports-api (added by Module 6's Terraform state)"
}

output "module6_service_sg_id" {
  value = aws_security_group.module6_service.id
}
