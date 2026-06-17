# ── Ingestion Bucket ──────────────────────────────────────────────────────
resource "aws_s3_bucket" "ingestion" {

  bucket        = "promptflow-ingestion-${var.environment}"
  force_destroy = var.environment != "prod"

}


resource "aws_s3_bucket_versioning" "ingestion" {

  bucket = aws_s3_bucket.ingestion.id
  versioning_configuration {

    status = "Enabled"

  }


}


resource "aws_s3_bucket_public_access_block" "ingestion" {

  bucket                  = aws_s3_bucket.ingestion.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

}


resource "aws_s3_bucket_server_side_encryption_configuration" "ingestion" {

  bucket = aws_s3_bucket.ingestion.id
  rule {

    apply_server_side_encryption_by_default {

      sse_algorithm = "AES256"

    }


  }


}


# NAAC 7-Year Retention Lifecycle
resource "aws_s3_bucket_lifecycle_configuration" "ingestion" {

  bucket = aws_s3_bucket.ingestion.id

  rule {

    id     = "naac-7year-retention"
    status = "Enabled"
    filter {

      prefix = ""

    }


    # 0-30 days: STANDARD (default, no transition needed)
    transition {

      days          = 30
      storage_class = "STANDARD_IA"

    }


    transition {

      days          = 365
      storage_class = "DEEP_ARCHIVE"

    }


    # NAAC requires 7-year minimum — do not expire before 2556 days
    expiration {

      days = 2556 # 7 years

    }


    noncurrent_version_expiration {

      noncurrent_days = 90

    }


  }


}


resource "aws_s3_bucket_policy" "ingestion" {

  bucket = aws_s3_bucket.ingestion.id
  policy = jsonencode({

    Version = "2012-10-17"
    Statement = [
      {

        Sid       = "DenyNonSSL"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.ingestion.arn,
          "${aws_s3_bucket.ingestion.arn}/*"
        ]
        Condition = {

          Bool = {
            "aws:SecureTransport" = "false"
          }


        }


      }

    ]

    }
  )

}


# ── Quarantine Bucket ─────────────────────────────────────────────────────
resource "aws_s3_bucket" "quarantine" {

  bucket        = "promptflow-quarantine-${var.environment}"
  force_destroy = var.environment != "prod"

}


resource "aws_s3_bucket_public_access_block" "quarantine" {

  bucket                  = aws_s3_bucket.quarantine.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true

}


resource "aws_s3_bucket_server_side_encryption_configuration" "quarantine" {

  bucket = aws_s3_bucket.quarantine.id
  rule {

    apply_server_side_encryption_by_default {

      sse_algorithm = "AES256"

    }


  }


}


resource "aws_s3_bucket_versioning" "quarantine" {

  bucket = aws_s3_bucket.quarantine.id
  versioning_configuration {

    status = "Enabled"

  }


}

