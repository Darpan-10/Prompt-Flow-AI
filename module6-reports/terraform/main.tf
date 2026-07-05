# terraform/main.tf
# Module 6: NAAC Report Generator — Root Module
#
# Like Module 5, Module 6 owns no VPC/RDS/Redis/KMS of its own -- it
# reuses Module 4's already-deployed infrastructure via data lookups.
# Module 6's ONE piece of genuinely new infrastructure is its own S3
# bucket for storing generated report files (Module 4/5 have no
# equivalent -- Module 4's S3 usage is Module 2/3's ingestion bucket, a
# completely separate bucket for a completely different purpose).

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # backend "s3" {
  #   bucket = "promptflow-terraform-state-ap-south-1"
  #   key    = "module6/terraform.tfstate"
  #   region = "ap-south-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

# ── Data sources: reuse Module 4's existing VPC + subnets ────────────────────

data "aws_vpc" "main" {
  filter {
    name   = "tag:Name"
    values = ["promptflow-${var.environment}-vpc"]
  }
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }
  tags = {
    Tier = "private"
  }
}

# ── Data source: reuse Module 4's RDS security group (Module 6 adds an ──────
# ingress rule to it, same cross-stack aws_security_group_rule pattern
# Module 5 uses -- see modules/security_groups/main.tf)

data "aws_security_group" "module4_rds" {
  filter {
    name   = "tag:Name"
    values = ["promptflow-rds-${var.environment}"]
  }
}

# ── Data source: reuse Module 4's shared KMS key ──────────────────────────────

data "aws_kms_alias" "promptflow" {
  name = "alias/promptflow-${var.environment}"
}

# ── Data source: reuse Module 4's existing DB credentials secret ─────────────

data "aws_secretsmanager_secret" "db_credentials" {
  name = "/promptflow/${var.environment}/db-credentials"
}

# ── Module 6's own S3 bucket for generated reports ────────────────────────────

module "s3" {
  source = "./modules/s3"

  environment           = var.environment
  kms_key_arn            = data.aws_kms_alias.promptflow.target_key_arn
  access_log_bucket_id   = var.access_log_bucket_id
}

# ── Security group ─────────────────────────────────────────────────────────────

module "security_groups" {
  source = "./modules/security_groups"

  environment       = var.environment
  vpc_id            = data.aws_vpc.main.id
  vpc_cidr          = data.aws_vpc.main.cidr_block
  module4_rds_sg_id = data.aws_security_group.module4_rds.id
}

# ── ECS Fargate + IAM (commented out until ECR repo + ALB are provisioned) ───
# Uncomment once you have:
#   1. Pushed the Module 6 Docker image to ECR
#   2. Created an ALB + target group (or reused one, once it exists)
#   3. Obtained Module 1's RS256 public key for production JWT verification
#   4. Filled in the corresponding variables below
#
# module "iam" {
#   source = "./modules/iam"
#
#   environment          = var.environment
#   aws_region           = var.aws_region
#   db_secret_arn        = data.aws_secretsmanager_secret.db_credentials.arn
#   reports_bucket_arn   = module.s3.bucket_arn
#   kms_key_arn          = data.aws_kms_alias.promptflow.target_key_arn
# }
#
# module "ecs" {
#   source = "./modules/ecs"
#
#   environment                = var.environment
#   private_subnet_ids         = data.aws_subnets.private.ids
#   service_security_group_id  = module.security_groups.module6_service_sg_id
#   ecs_execution_role_arn     = module.iam.ecs_execution_role_arn
#   ecs_task_role_arn          = module.iam.ecs_task_role_arn
#
#   ecr_repository_url    = var.ecr_repository_url
#   image_tag             = var.image_tag
#   db_secret_arn          = data.aws_secretsmanager_secret.db_credentials.arn
#   reports_bucket_name    = module.s3.bucket_name
#   jwt_public_key         = var.jwt_public_key
#   kms_key_arn            = data.aws_kms_alias.promptflow.target_key_arn
#   target_group_arn       = var.target_group_arn
#   alb_listener_arn       = var.alb_listener_arn
# }

# ── Outputs ─────────────────────────────────────────────────────────────────────

output "reports_bucket_name" {
  value = module.s3.bucket_name
}

output "reports_bucket_arn" {
  value = module.s3.bucket_arn
}

output "module6_service_sg_id" {
  value = module.security_groups.module6_service_sg_id
}

output "vpc_id" {
  value = data.aws_vpc.main.id
}

output "kms_key_arn" {
  value = data.aws_kms_alias.promptflow.target_key_arn
}
