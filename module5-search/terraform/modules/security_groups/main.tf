# terraform/modules/security_groups/main.tf
# Module 5: Security Group for the search-api ECS task, plus the
# cross-stack ingress rules that let it actually reach Module 4's
# RDS/Redis security groups without modifying Module 4's Terraform state.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ── Security Group: Module 5 ECS Service ──────────────────────────────────────

# NOTE on checkov finding CKV2_AWS_5 ("Security Group attached to another
# resource") for this SG: genuinely not yet attached -- the ECS module
# that would reference it (modules/ecs) is intentionally commented out in
# root main.tf pending ECR repo URL / ALB ARNs / JWT public key inputs.
# This is a REAL gap, not a false positive -- it will resolve once
# modules/ecs is uncommented and wired to service_security_group_id =
# module.security_groups.module5_service_sg_id. (Inline #checkov:skip
# does not work for this specific check regardless -- CKV2_AWS_5 is a
# graph-based check with a confirmed upstream checkov bug where inline
# suppression of "is this resource attached" checks is silently ignored;
# see Module 4's TERRAFORM_TESTING.md for the verification steps and
# more detail -- the exact same situation applies here.)
resource "aws_security_group" "module5_service" {
  name        = "module5-search-service-${var.environment}"
  description = "Module 5 search-api FastAPI service"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 8005
    to_port     = 8005
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "FastAPI search-api from within VPC (ALB)"
  }

  # Scoped egress, same rationale as Module 4's module4_service SG:
  # specific ports only, never a blanket -1/all-ports rule. Module 5 has
  # no Kafka dependency (it's read-only against Postgres -- no Kafka
  # consumer), so unlike Module 4's service SG there's no MSK broker
  # rule here.
  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS to AWS APIs (Secrets Manager, ECR, CloudWatch Logs)"
  }

  egress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "PostgreSQL to Module 4's RDS within VPC"
  }

  egress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Redis to Module 4's ElastiCache within VPC"
  }

  egress {
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = [var.vpc_cidr]
    description = "DNS resolution within VPC"
  }

  tags = {
    Name        = "module5-search-service-${var.environment}"
    Environment = var.environment
    Module      = "Module5_Search"
  }
}

# ── Cross-stack rules: grant Module 5 ingress into Module 4's RDS + Redis ─────
#
# These are standalone aws_security_group_rule resources, NOT inline
# blocks inside a security group resource -- that distinction matters.
# An inline `ingress {}` block inside an `aws_security_group` resource
# tells Terraform "this is the COMPLETE set of rules for this SG", which
# would conflict with Module 4's own state (which also manages inline
# rules on these same security groups) and cause perpetual plan diffs
# between the two states fighting over the rule set.
#
# A standalone aws_security_group_rule, by contrast, manages exactly ONE
# rule and coexists peacefully with rules defined elsewhere (including in
# a completely different Terraform state), as long as nobody defines the
# exact same rule twice. This is the standard, supported pattern for one
# Terraform state to extend a security group owned by another state.

resource "aws_security_group_rule" "module5_to_rds" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = var.module4_rds_sg_id
  source_security_group_id = aws_security_group.module5_service.id
  description               = "PostgreSQL from Module 5 search-api (added by Module 5's Terraform state)"
}

resource "aws_security_group_rule" "module5_to_redis" {
  type                     = "ingress"
  from_port                = 6379
  to_port                  = 6379
  protocol                 = "tcp"
  security_group_id        = var.module4_redis_sg_id
  source_security_group_id = aws_security_group.module5_service.id
  description               = "Redis from Module 5 search-api (added by Module 5's Terraform state)"
}

output "module5_service_sg_id" {
  value = aws_security_group.module5_service.id
}
