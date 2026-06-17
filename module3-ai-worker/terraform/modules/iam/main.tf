resource "aws_iam_role" "worker" {

  name = "promptflow-m3-${var.environment}-worker"
  assume_role_policy = jsonencode({

    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }

      Action = "sts:AssumeRole"
      }
    ]

    }
  )

}


# S3: GetObject on ingestion bucket only (to download attachments for hash check)
resource "aws_iam_role_policy" "s3_read" {

  name = "s3-ingestion-get"
  role = aws_iam_role.worker.id
  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [{
      Sid      = "S3GetIngestion"
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${var.s3_ingestion_bucket_arn}/*"
      }
    ]

    }
  )

}



# Secrets Manager: read specific ARNs only
resource "aws_iam_role_policy" "secrets" {

  name = "secrets-read"
  role = aws_iam_role.worker.id
  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [{
      Sid      = "SecretsRead"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secrets_arns
      }
    ]

    }
  )

}


# Bedrock: invoke claude-3-haiku only
resource "aws_iam_role_policy" "bedrock" {

  name = "bedrock-invoke-haiku"
  role = aws_iam_role.worker.id
  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [{

      Sid      = "BedrockInvokeHaiku"
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = "arn:aws:bedrock:ap-south-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0"

      }
    ]

    }
  )

}


# RDS: IAM connect
resource "aws_iam_role_policy" "rds" {

  name = "rds-iam-connect"
  role = aws_iam_role.worker.id
  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [{
      Sid      = "RDSConnect"
      Effect   = "Allow"
      Action   = ["rds-db:connect"]
      Resource = "arn:aws:rds-db:${data.aws_region.current.name}:*:dbuser/promptflow_admin"
      }
    ]

    }
  )

}


data "aws_region" "current" {

}

