resource "aws_cognito_user_pool" "main" {
  name = "promptflow-m3-${var.environment}"
  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = true
    require_uppercase = true
  }

  schema {
    name                = "department_code"
    attribute_data_type = "String"
    mutable             = true
    required            = false
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

}

resource "aws_cognito_user_pool_client" "m2m" {
  name         = "promptflow-m3-worker"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = true
}

