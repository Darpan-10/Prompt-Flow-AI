# terraform/main.tf
# Module 4: Root module — wires RDS, ElastiCache, Security Groups, IAM

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Uncomment once the state bucket exists:
  # backend "s3" {
  #   bucket = "promptflow-terraform-state-ap-south-1"
  #   key    = "module4/terraform.tfstate"
  #   region = "ap-south-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

# ── Data sources: reuse the EXISTING VPC created by Module 1-3's vpc.tf ──────
# That VPC is tagged: Name = "promptflow-${var.environment}-vpc"
# (confirmed against the actual aws_vpc.main resource in the locked stack)

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
}

# ── Shared KMS Key (encrypts RDS, Secrets Manager, ElastiCache, CloudWatch Logs) ──

module "kms" {
  source = "./modules/kms"

  environment = var.environment
  aws_region  = var.aws_region
}

# ── Security Groups ────────────────────────────────────────────────────────────

module "security_groups" {
  source = "./modules/security_groups"

  environment                = var.environment
  vpc_id                     = data.aws_vpc.main.id
  vpc_cidr                   = data.aws_vpc.main.cidr_block
  module3_security_group_ids = var.module3_security_group_ids
}

# ── RDS PostgreSQL ─────────────────────────────────────────────────────────────

module "rds" {
  source = "./modules/rds"

  environment            = var.environment
  private_subnet_ids     = data.aws_subnets.private.ids
  security_group_id      = module.security_groups.rds_sg_id
  db_instance_class      = var.db_instance_class
  allocated_storage      = var.allocated_storage
  max_allocated_storage  = var.max_allocated_storage
  db_password            = var.db_password
  kms_key_arn            = module.kms.key_arn
}

# ── ElastiCache Redis ───────────────────────────────────────────────────────────

module "elasticache" {
  source = "./modules/elasticache"

  environment        = var.environment
  private_subnet_ids = data.aws_subnets.private.ids
  security_group_id  = module.security_groups.redis_sg_id
  node_type          = var.redis_node_type
  redis_auth_token   = var.redis_auth_token
  kms_key_arn        = module.kms.key_arn
}

# ── IAM Roles ───────────────────────────────────────────────────────────────────

module "iam" {
  source = "./modules/iam"

  environment       = var.environment
  aws_region        = var.aws_region
  db_secret_arn     = module.rds.db_secret_arn
  redis_secret_arn  = ""
  s3_bucket_arn     = var.s3_bucket_arn
}

# ── ECS Fargate (commented out until ECR repo + ALB are provisioned) ─────────
# Uncomment once you have:
#   1. Pushed the Module 4 Docker image to ECR
#   2. Created an ALB + target group (or reused Module 3's ALB)
#   3. Filled in the corresponding variables below
#
# module "ecs" {
#   source = "./modules/ecs"
#
#   environment                = var.environment
#   private_subnet_ids         = data.aws_subnets.private.ids
#   service_security_group_id  = module.security_groups.module4_service_sg_id
#   ecs_execution_role_arn     = module.iam.ecs_execution_role_arn
#   ecs_task_role_arn          = module.iam.ecs_task_role_arn
#
#   ecr_repository_url      = var.ecr_repository_url
#   image_tag               = var.image_tag
#   db_secret_arn            = module.rds.db_secret_arn
#   redis_secret_arn         = var.redis_secret_arn  # create a secret wrapping module.elasticache.redis_url
#   kafka_bootstrap_brokers  = var.kafka_bootstrap_brokers
#   target_group_arn         = var.target_group_arn
#   alb_listener_arn          = var.alb_listener_arn
#   kms_key_arn               = module.kms.key_arn
# }


# ── Outputs ─────────────────────────────────────────────────────────────────────

output "rds_endpoint" {
  value = module.rds.rds_endpoint
}

output "database_url" {
  value     = module.rds.database_url
  sensitive = true
}

output "redis_endpoint" {
  value = module.elasticache.redis_endpoint
}

output "redis_url" {
  value     = module.elasticache.redis_url
  sensitive = true
}

output "ecs_execution_role_arn" {
  value = module.iam.ecs_execution_role_arn
}

output "ecs_task_role_arn" {
  value = module.iam.ecs_task_role_arn
}

output "module4_service_sg_id" {
  value = module.security_groups.module4_service_sg_id
}

output "kms_key_arn" {
  value = module.kms.key_arn
}
