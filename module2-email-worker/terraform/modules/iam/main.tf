# Worker execution role
resource "aws_iam_role" "worker" {

  name = "promptflow-${var.environment}-module2-worker"

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


# S3: PutObject on specific buckets ONLY — NO wildcard
resource "aws_iam_role_policy" "s3_ingestion" {

  name = "s3-ingestion-put"
  role = aws_iam_role.worker.id

  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [
      {

        Sid      = "AllowPutIngestion"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${var.s3_ingestion_bucket_arn}/*"

      }
      ,
      {

        Sid      = "AllowPutQuarantine"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${var.s3_quarantine_bucket_arn}/*"

      }

    ]

    }
  )

}


# Secrets Manager: read SPECIFIC ARNs only — NO wildcard
resource "aws_iam_role_policy" "secrets" {

  name = "secrets-manager-read"
  role = aws_iam_role.worker.id

  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [{

      Sid      = "AllowSecretRead"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secrets_arns

      }
    ]

    }
  )

}



# CloudWatch: emit custom metrics
resource "aws_iam_role_policy" "cloudwatch" {

  name = "cloudwatch-metrics"
  role = aws_iam_role.worker.id

  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [{

      Sid      = "AllowMetricEmit"
      Effect   = "Allow"
      Action   = ["cloudwatch:PutMetricData"]
      Resource = "*"
      Condition = {

        StringEquals = {

          "cloudwatch:namespace" = "PromptFlow/EmailWorker"

        }


      }


      }
    ]

    }
  )

}

