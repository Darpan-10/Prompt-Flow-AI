# terraform/modules/kms/main.tf
# Module 4: Shared Customer-Managed KMS Key
#
# Added in response to checkov findings (CKV_AWS_149, CKV_AWS_191,
# CKV_AWS_354, CKV_AWS_158) which flagged RDS, Secrets Manager,
# ElastiCache, and CloudWatch Logs as using default AWS-owned encryption
# keys instead of a customer-managed key. A CMK gives you key rotation
# control, access-policy auditing, and the ability to revoke access
# independently of AWS's own key lifecycle -- relevant for NAAC/FERPA
# compliance reviews where "who can decrypt this data" needs to be a
# concrete, auditable answer.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "promptflow" {
  description             = "PromptFlow Module 4 shared CMK (RDS, Secrets Manager, ElastiCache, CloudWatch Logs) - ${var.environment}"
  deletion_window_in_days = var.environment == "prod" ? 30 : 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "EnableRootAccountFullAccess"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid    = "AllowCloudWatchLogsEncryption"
        Effect = "Allow"
        Principal = {
          Service = "logs.${var.aws_region}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt*",
          "kms:Decrypt*",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:Describe*",
        ]
        Resource = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
          }
        }
      },
    ]
  })

  tags = {
    Name        = "promptflow-${var.environment}-cmk"
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

resource "aws_kms_alias" "promptflow" {
  name          = "alias/promptflow-${var.environment}"
  target_key_id = aws_kms_key.promptflow.key_id
}

output "key_arn" {
  value = aws_kms_key.promptflow.arn
}

output "key_id" {
  value = aws_kms_key.promptflow.key_id
}
