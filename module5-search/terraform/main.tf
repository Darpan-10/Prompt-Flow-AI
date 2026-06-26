# terraform/main.tf
# Module 5: Search & Discovery — Root Module
#
# Module 5 owns NO data infrastructure of its own. It is a read-only
# consumer of Module 4's RDS PostgreSQL, ElastiCache Redis, KMS key, and
# Secrets Manager entries (per the locked architecture decision). This
# root module therefore consists almost entirely of `data` lookups
# against Module 4's already-deployed resources, plus Module 5's own
# ECS Fargate compute layer and the security group rules needed to let
# that compute layer reach Module 4's database/cache.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment once the state bucket exists (same bucket Module 4 uses,
  # different key so the two states stay independent):
  # backend "s3" {
  #   bucket = "promptflow-terraform-state-ap-south-1"
  #   key    = "module5/terraform.tfstate"
  #   region = "ap-south-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

# ── Data sources: reuse Module 4's existing VPC + subnets ────────────────────
# Same VPC tag pattern as Module 4's root main.tf -- see Module 4's
# TERRAFORM_TESTING.md / main.tf comments for why this is a data lookup
# rather than a fresh aws_vpc resource.

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

# ── Data sources: reuse Module 4's existing RDS + Redis security groups ──────
# Module 5 needs to ADD an ingress rule to each of these (allowing
# connections from Module 5's own ECS task security group), without
# touching Module 4's Terraform state or code at all. This is done below
# via standalone aws_security_group_rule resources, which is the
# supported pattern for one Terraform state to add rules to a security
# group owned by a different state.

data "aws_security_group" "module4_rds" {
  filter {
    name   = "tag:Name"
    values = ["promptflow-rds-${var.environment}"]
  }
}

data "aws_security_group" "module4_redis" {
  filter {
    name   = "tag:Name"
    values = ["promptflow-redis-${var.environment}"]
  }
}

# ── Data source: reuse Module 4's shared KMS key ──────────────────────────────
# Used to encrypt Module 5's own CloudWatch log group, so log encryption
# is consistent with the rest of the stack without provisioning a second
# customer-managed key just for one log group.

data "aws_kms_alias" "promptflow" {
  name = "alias/promptflow-${var.environment}"
}

# ── Data sources: reuse Module 4's existing Secrets Manager entries ──────────
# Module 5 reads the SAME database credentials Module 4 writes with (no
# separate read-only DB user has been provisioned yet -- see the note in
# TERRAFORM_TESTING.md about this being a follow-up, not an oversight).

data "aws_secretsmanager_secret" "db_credentials" {
  name = "/promptflow/${var.environment}/db-credentials"
}

# ── Module 5's own security group (ECS task) ──────────────────────────────────

module "security_groups" {
  source = "./modules/security_groups"

  environment       = var.environment
  vpc_id            = data.aws_vpc.main.id
  vpc_cidr          = data.aws_vpc.main.cidr_block
  module4_rds_sg_id   = data.aws_security_group.module4_rds.id
  module4_redis_sg_id = data.aws_security_group.module4_redis.id
}

# ── ECS Fargate (commented out until ECR repo + ALB are provisioned) ─────────
# Uncomment once you have:
#   1. Pushed the Module 5 Docker image to ECR
#   2. Created an ALB + target group (or reused Module 4's, once that
#      exists -- Module 4's own ECS deployment is ALSO pending an ALB,
#      see Module 4's terraform/main.tf)
#   3. Obtained Module 1's RS256 public key for production JWT
#      verification (SKIP_JWT_VALIDATION must be false in any deployed
#      environment -- see SETUP.md section 4)
#   4. Filled in the corresponding variables below
#
# module "ecs" {
#   source = "./modules/ecs"
#
#   environment                = var.environment
#   private_subnet_ids         = data.aws_subnets.private.ids
#   service_security_group_id  = module.security_groups.module5_service_sg_id
#   ecs_execution_role_arn     = module.iam.ecs_execution_role_arn
#   ecs_task_role_arn          = module.iam.ecs_task_role_arn
#
#   ecr_repository_url   = var.ecr_repository_url
#   image_tag            = var.image_tag
#   db_secret_arn        = data.aws_secretsmanager_secret.db_credentials.arn
#   redis_url            = var.redis_url  # plain Terraform var input -- modules/ecs wraps it in its OWN Secrets Manager entry internally (see modules/ecs/main.tf), never injected as plaintext into the task definition
#   jwt_public_key       = var.jwt_public_key
#   kms_key_arn          = data.aws_kms_alias.promptflow.target_key_arn
#   target_group_arn     = var.target_group_arn
#   alb_listener_arn     = var.alb_listener_arn
# }
#
# module "iam" {
#   source = "./modules/iam"
#
#   environment       = var.environment
#   aws_region        = var.aws_region
#   db_secret_arn     = data.aws_secretsmanager_secret.db_credentials.arn
# }

# ── Outputs ─────────────────────────────────────────────────────────────────────

output "module5_service_sg_id" {
  value = module.security_groups.module5_service_sg_id
}

output "vpc_id" {
  value = data.aws_vpc.main.id
}

output "kms_key_arn" {
  value = data.aws_kms_alias.promptflow.target_key_arn
}
