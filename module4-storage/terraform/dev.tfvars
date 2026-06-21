aws_region  = "ap-south-1"
environment = "dev"
# VPC is looked up automatically by tag "promptflow-dev-vpc" -- no variable needed.

# Module 3 security groups (fill in from Module 3 Terraform output, if needed)
module3_security_group_ids = []

db_instance_class     = "db.t4g.small"
allocated_storage     = 50
max_allocated_storage = 200

# Generate with: openssl rand -hex 32
# db_password = "REPLACE_ME"

redis_node_type = "cache.t4g.micro"
# Generate with: openssl rand -hex 32
# redis_auth_token = "REPLACE_ME"

# Fill in from Module 2/3 Terraform output
s3_bucket_arn = "arn:aws:s3:::promptflow-ingestion-dev"
