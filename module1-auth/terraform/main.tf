# ============================================================
# Prompt Flow AI — Module 1: AWS Infrastructure
# Terraform v1.6+
#
# Resources:
#   - Cognito User Pool (web + M2M clients, SAML federation)
#   - RDS PostgreSQL 15
#   - ElastiCache Redis 7
#   - CloudWatch Alarms + SNS
# ============================================================

terraform {

  required_providers {

    aws = {

      source  = "hashicorp/aws"
      version = "~> 5.0"

    }


  }

  required_version = ">= 1.6"

}


provider "aws" {

  region = var.aws_region

}


# ── Variables ─────────────────────────────────────────────
variable "aws_region" {

  default = "ap-south-1"

}

variable "env" {

  default = "prod"

}

variable "db_password" {

  sensitive = true

}

variable "alert_email" {

  description = "Email for CloudWatch alerts"

}

variable "callback_url" {

  default = "https://api.promptflow.ai/auth/callback"

}

variable "cognito_domain" {

  default = "auth-promptflow"

}


# ── Cognito User Pool ──────────────────────────────────────
resource "aws_cognito_user_pool" "main" {

  name = "promptflow-${var.env}-users"

  password_policy {

    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7

  }


  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {

    enabled = true

  }


  # Custom attributes mapped from SRM SSO
  schema {

    name                = "department_code"
    attribute_data_type = "String"
    mutable             = true
    required            = false
    string_attribute_constraints {

      min_length = 2
      max_length = 20

    }


  }


  schema {

    name                = "role"
    attribute_data_type = "String"
    mutable             = true
    required            = false
    string_attribute_constraints {

      min_length = 1
      max_length = 20

    }


  }


  admin_create_user_config {

    allow_admin_create_user_only = false

  }


  auto_verified_attributes = ["email"]

  tags = {
    Project = "PromptFlowAI", Module = "1", Env = var.env
  }


}


# ── Web App Client (OAuth2 + PKCE) ────────────────────────
resource "aws_cognito_user_pool_client" "web" {

  name         = "promptflow-web-client"
  user_pool_id = aws_cognito_user_pool.main.id

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = [var.callback_url]
  logout_urls                          = ["https://api.promptflow.ai/auth/logout"]

  # PKCE: no client secret needed on frontend
  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  token_validity_units {

    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"

  }

  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 7

}


# ── M2M Service Client (Client Credentials) ───────────────
resource "aws_cognito_user_pool_client" "m2m" {

  name         = "promptflow-m2m-client"
  user_pool_id = aws_cognito_user_pool.main.id

  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["queue.consume", "db.write.internal"]

  generate_secret = true # M2M needs client secret

}


# ── Cognito Custom Domain ──────────────────────────────────
resource "aws_cognito_user_pool_domain" "main" {

  domain       = var.cognito_domain
  user_pool_id = aws_cognito_user_pool.main.id

}


# ── SAML Identity Provider (SRM AP SSO) ───────────────────
# Uncomment and provide saml-metadata.xml after getting it from SRM IT
# resource "aws_cognito_identity_provider" "srmap_saml" {
#   user_pool_id  = aws_cognito_user_pool.main.id
#   provider_name = "SRMAP-SSO"
#   provider_type = "SAML"
#   provider_details = {
#     MetadataFile = file("saml-metadata.xml")
#     IDPSignout   = true
#     AttributeMapping = {
#       email           = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
#       custom:department_code = "http://schemas.srmap.edu.in/department"
#       custom:role     = "http://schemas.srmap.edu.in/role"
#     }
#   }
# }

# ── SNS Alert Topic ───────────────────────────────────────
resource "aws_sns_topic" "alerts" {

  name = "promptflow-${var.env}-alerts"

}


resource "aws_sns_topic_subscription" "email_alerts" {

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email

}


resource "aws_sns_topic" "security_alerts" {

  name = "promptflow-${var.env}-security-alerts"

}


resource "aws_sns_topic_subscription" "security_email" {

  topic_arn = aws_sns_topic.security_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email

}


# ── CloudWatch Alarms ─────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "auth_latency" {

  alarm_name          = "promptflow-auth-p95-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "p95"
  threshold           = 1500
  alarm_description   = "Auth service p95 latency > 1.5s"
  alarm_actions       = [aws_sns_topic.alerts.arn]

}


resource "aws_cloudwatch_metric_alarm" "failed_logins" {

  alarm_name          = "promptflow-auth-failed-logins"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "4xxErrorCount"
  namespace           = "PromptFlow/Auth"
  period              = 300
  statistic           = "Sum"
  threshold           = 50
  alarm_description   = "High failed login rate"
  alarm_actions       = [aws_sns_topic.security_alerts.arn]

}


# ── Outputs ───────────────────────────────────────────────
output "cognito_user_pool_id" {

  value = aws_cognito_user_pool.main.id

}


output "cognito_web_client_id" {

  value = aws_cognito_user_pool_client.web.id

}


output "cognito_m2m_client_id" {

  value = aws_cognito_user_pool_client.m2m.id

}


output "cognito_domain_url" {

  value = "https://${var.cognito_domain}.auth.${var.aws_region}.amazoncognito.com"

}

