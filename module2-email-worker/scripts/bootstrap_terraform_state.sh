#!/usr/bin/env bash
# scripts/bootstrap_terraform_state.sh
# Run ONCE before first `terraform init`.
# Creates the S3 state bucket and DynamoDB lock table.

set -euo pipefail

REGION="ap-south-1"
STATE_BUCKET="promptflow-terraform-state-ap"
LOCK_TABLE="promptflow-terraform-locks"

echo "=== Bootstrapping Terraform State Backend ==="
echo "Region: $REGION"
echo "Bucket: $STATE_BUCKET"
echo "Lock Table: $LOCK_TABLE"
echo ""

# Create S3 state bucket
echo "Creating S3 state bucket..."
aws s3api create-bucket \
  --bucket "$STATE_BUCKET" \
  --region "$REGION" \
  --create-bucket-configuration LocationConstraint="$REGION"

# Enable versioning on state bucket
aws s3api put-bucket-versioning \
  --bucket "$STATE_BUCKET" \
  --versioning-configuration Status=Enabled

# Enable encryption
aws s3api put-bucket-encryption \
  --bucket "$STATE_BUCKET" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'

# Block all public access on state bucket
aws s3api put-public-access-block \
  --bucket "$STATE_BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "✅ S3 state bucket created and secured"

# Create DynamoDB lock table
echo "Creating DynamoDB lock table..."
aws dynamodb create-table \
  --table-name "$LOCK_TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "$REGION"

echo "✅ DynamoDB lock table created"
echo ""
echo "=== Bootstrap Complete ==="
echo ""
echo "Next steps:"
echo "  cd terraform/"
echo "  terraform init"
echo "  terraform plan -var-file=dev.tfvars"
echo "  terraform apply -var-file=dev.tfvars"
