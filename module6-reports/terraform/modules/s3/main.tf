# terraform/modules/s3/main.tf
# Module 6: S3 bucket for generated reports (PDF/Excel), encrypted with
# Module 4's shared CMK, with a lifecycle policy matching NAAC's 7-year
# retention expectation (same pattern Module 2/3 used for raw ingestion
# attachments).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_s3_bucket" "reports" {
  bucket = "promptflow-reports-${var.environment}"

  tags = {
    Name        = "promptflow-reports-${var.environment}"
    Environment = var.environment
    Module      = "Module6_Reports"
    Compliance  = "NAAC-7yr-retention"
  }
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "naac-7yr-retention"
    status = "Enabled"

    filter {}

    # CKV_AWS_300: clean up abandoned multipart uploads (e.g. from a
    # crashed/retried upload) after 7 days so they don't accumulate
    # storage cost indefinitely.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }

    # Move to cheaper storage after 90 days (reports are rarely
    # re-downloaded once a compliance cycle closes), expire after the
    # NAAC-mandated 7-year retention window.
    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 365
      storage_class = "GLACIER"
    }
    expiration {
      days = 2557  # 7 years
    }

    noncurrent_version_expiration {
      noncurrent_days = 2557
    }
  }
}

resource "aws_s3_bucket_logging" "reports" {
  # CRITICAL: AWS does NOT support delivering S3 server access logs to a
  # destination bucket that uses SSE-KMS encryption -- the destination
  # bucket MUST use SSE-S3 (AES256). Since aws_s3_bucket.reports itself
  # uses aws:kms encryption (see
  # aws_s3_bucket_server_side_encryption_configuration.reports above),
  # self-logging (target_bucket = this same bucket) would silently fail
  # -- logs might not be created at all, or could be created but
  # encrypted with a key you can't read them with. Verified via AWS's
  # own documentation: "If a bucket is used as a destination for Amazon
  # S3 server access logging, the destination bucket must use Amazon S3
  # managed keys (SSE-S3)."
  #
  # Logging is therefore only enabled when a real, separately-managed
  # SSE-S3 logging bucket ARN is explicitly provided via
  # var.access_log_bucket_id -- it does NOT default to self-logging.
  count = var.access_log_bucket_id != "" ? 1 : 0

  bucket = aws_s3_bucket.reports.id

  target_bucket = var.access_log_bucket_id
  target_prefix = "access-logs/promptflow-reports-${var.environment}/"
}

# CKV2_AWS_62: event notification on ObjectCreated, published to an SNS
# topic. Genuinely useful here (not just satisfying the checker) --
# gives downstream systems (e.g. a future notification service telling a
# coordinator "your report is ready") a real hook without polling
# GET /reports/{id}.
resource "aws_sns_topic" "report_events" {
  name              = "promptflow-reports-${var.environment}-events"
  kms_master_key_id = var.kms_key_arn

  tags = {
    Environment = var.environment
    Module      = "Module6_Reports"
  }
}

resource "aws_sns_topic_policy" "report_events" {
  arn = aws_sns_topic.report_events.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3Publish"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.report_events.arn
      Condition = {
        ArnLike = { "aws:SourceArn" = aws_s3_bucket.reports.arn }
      }
    }]
  })
}

resource "aws_s3_bucket_notification" "reports" {
  bucket = aws_s3_bucket.reports.id

  topic {
    topic_arn = aws_sns_topic.report_events.arn
    events    = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_sns_topic_policy.report_events]
}

# NOTE on checkov finding CKV_AWS_144 ("cross-region replication"):
# deliberately NOT enabled. NAAC compliance reports for SRM AP are a
# single-region (ap-south-1), single-institution workload with no
# multi-region disaster-recovery requirement in the current spec --
# enabling CRR would double storage cost and add a second KMS key +
# cross-region IAM role for a durability guarantee (protection against
# an entire AWS region outage) that isn't a stated requirement anywhere
# in the locked spec. Versioning (enabled above) already protects
# against accidental overwrite/deletion, which is the failure mode that
# actually matters for this use case. Revisit if a real DR requirement
# is introduced later.

output "bucket_name" {
  value = aws_s3_bucket.reports.id
}

output "bucket_arn" {
  value = aws_s3_bucket.reports.arn
}

output "sns_topic_arn" {
  value = aws_sns_topic.report_events.arn
}
