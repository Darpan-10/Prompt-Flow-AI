resource "aws_cognito_user_pool" "main" {

  name = "promptflow-${var.environment}-users"

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


  # Custom attributes for RBAC
  schema {

    name                = "department_code"
    attribute_data_type = "String"
    mutable             = true
    required            = false
    string_attribute_constraints {

      min_length = 1
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


  account_recovery_setting {

    recovery_mechanism {

      name     = "verified_email"
      priority = 1

    }


  }


  admin_create_user_config {

    allow_admin_create_user_only = true

  }


}


# ── M2M App Client for Module 2 Worker ───────────────────────────────────
resource "aws_cognito_user_pool_client" "m2m_worker" {

  depends_on = [aws_cognito_resource_server.worker]

  name         = "promptflow-module2-worker"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = true

  # Client Credentials only — no user-level auth
  allowed_oauth_flows                  = ["client_credentials"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes = [
    "https://api.promptflow.ai/queue.consume",
    "https://api.promptflow.ai/s3.write.ingestion"
  ]
  explicit_auth_flows = []

}


# ── Resource Server (scopes definition) ──────────────────────────────────
resource "aws_cognito_resource_server" "worker" {

  identifier   = "https://api.promptflow.ai"
  name         = "PromptFlow API"
  user_pool_id = aws_cognito_user_pool.main.id

  scope {

    scope_name        = "queue.consume"
    scope_description = "Consume from Kafka queue"

  }


  scope {

    scope_name        = "s3.write.ingestion"
    scope_description = "Write to S3 ingestion bucket"

  }


}

