aws_region  = "ap-south-1"
environment = "dev"
# VPC, subnets, Module 4's RDS/Redis security groups, and Module 4's KMS
# key are all looked up automatically via data sources in main.tf -- no
# variables needed for those.

# Fill in once you have an ECR repo for Module 5's image:
# ecr_repository_url = "123456789.dkr.ecr.ap-south-1.amazonaws.com/promptflow-module5"

# Fill in from Module 4's `terraform output -raw redis_url`:
# redis_url = "rediss://:TOKEN@module4-redis-endpoint:6379"

# Fill in from Module 1 once it issues RS256 JWTs:
# jwt_public_key = "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"

# Fill in once an ALB target group exists for Module 5:
# target_group_arn = "arn:aws:elasticloadbalancing:..."
# alb_listener_arn  = "arn:aws:elasticloadbalancing:..."
