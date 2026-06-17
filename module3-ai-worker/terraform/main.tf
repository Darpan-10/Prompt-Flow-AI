terraform {

  required_version = ">= 1.6.0"
  required_providers {

    aws = {
      source = "hashicorp/aws", version = "~> 5.0"
    }


  }

  backend "s3" {

    bucket         = "promptflow-terraform-state-ap"
    key            = "module3/ai-worker/terraform.tfstate"
    region         = "ap-south-1"
    encrypt        = true
    dynamodb_table = "promptflow-terraform-locks"

  }


}


provider "aws" {

  region = var.aws_region
  default_tags {

    tags = {

      Project     = "PromptFlowAI"
      Module      = "module3-ai-worker"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Compliance  = "NAAC-FERPA"

    }


  }


}


module "vpc" {
  source      = "./modules/vpc"
  environment = var.environment
  aws_region  = var.aws_region
}

module "s3" {
  source      = "./modules/s3"
  environment = var.environment
  aws_region  = var.aws_region
}

module "rds" {
  source             = "./modules/rds"
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  db_password        = var.db_password
}

module "elasticache" {
  source             = "./modules/elasticache"
  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  auth_token         = var.redis_auth_token
}


module "cognito" {
  source      = "./modules/cognito"
  environment = var.environment
}

module "secrets" {
  source                  = "./modules/secrets"
  environment             = var.environment
  db_password             = var.db_password
  redis_auth_token        = var.redis_auth_token
  kafka_bootstrap_servers = "localhost:9092"
}

module "iam" {
  source                  = "./modules/iam"
  environment             = var.environment
  s3_ingestion_bucket_arn = module.s3.ingestion_bucket_arn
  secrets_arns            = module.secrets.secret_arns
}

