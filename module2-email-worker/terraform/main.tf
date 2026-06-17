provider "aws" {

  region = var.aws_region
  default_tags {

    tags = {

      Project     = "PromptFlowAI"
      Module      = "module2-email-worker"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Compliance  = "NAAC-FERPA"

    }


  }


}


# ── VPC ────────────────────────────────────────────────────────────────────
module "vpc" {

  source      = "./modules/vpc"
  environment = var.environment
  aws_region  = var.aws_region

}


# ── S3 Buckets ────────────────────────────────────────────────────────────
module "s3" {

  source      = "./modules/s3"
  environment = var.environment
  aws_region  = var.aws_region

}


# ── RDS PostgreSQL ────────────────────────────────────────────────────────
module "rds" {

  source             = "./modules/rds"
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  db_password        = var.db_password

}


# ── ElastiCache Redis ─────────────────────────────────────────────────────
module "elasticache" {

  source             = "./modules/elasticache"
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  auth_token         = var.redis_auth_token

}



# ── Cognito ───────────────────────────────────────────────────────────────
module "cognito" {

  source      = "./modules/cognito"
  environment = var.environment

}


# ── Secrets Manager ───────────────────────────────────────────────────────
module "secrets" {

  source                  = "./modules/secrets"
  environment             = var.environment
  db_password             = var.db_password
  redis_auth_token        = var.redis_auth_token
  imap_password           = var.imap_password
  kafka_bootstrap_servers = "localhost:9092"
  cognito_client_secret   = module.cognito.m2m_client_secret

}


# ── IAM ───────────────────────────────────────────────────────────────────
module "iam" {

  source                   = "./modules/iam"
  environment              = var.environment
  s3_ingestion_bucket_arn  = module.s3.ingestion_bucket_arn
  s3_quarantine_bucket_arn = module.s3.quarantine_bucket_arn
  secrets_arns             = module.secrets.secret_arns

}

