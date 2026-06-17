# terraform/dev.tfvars
# Usage: terraform apply -var-file=dev.tfvars
# NEVER commit real secrets — use AWS Secrets Manager or CI/CD injection

environment = "dev"
aws_region  = "ap-south-1"

# These are injected from CI/CD or local env — never hardcode
db_password      = "REPLACE_WITH_STRONG_PASSWORD"
redis_auth_token = "REPLACE_WITH_STRONG_TOKEN"
imap_password    = "REPLACE_FROM_SECRETS_MANAGER"
