resource "aws_secretsmanager_secret" "imap" {

  name                    = "promptflow/${var.environment}/imap-credentials"
  recovery_window_in_days = 7

}


resource "aws_secretsmanager_secret_version" "imap" {

  secret_id = aws_secretsmanager_secret.imap.id
  secret_string = jsonencode({

    gmail_delegated_user             = "papers@srmap.edu.in"
    google_service_account_json_path = "Inject from CI/CD pipeline"

    }
  )

}


resource "aws_secretsmanager_secret" "db" {

  name                    = "promptflow/${var.environment}/db-credentials"
  recovery_window_in_days = 7

}


resource "aws_secretsmanager_secret_version" "db" {

  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({

    username = "promptflow_admin"
    password = var.db_password
    engine   = "postgres"
    port     = 5432

    }
  )

}


resource "aws_secretsmanager_secret" "kafka" {

  name                    = "promptflow/${var.environment}/kafka-credentials"
  recovery_window_in_days = 7

}


resource "aws_secretsmanager_secret_version" "kafka" {

  secret_id = aws_secretsmanager_secret.kafka.id
  secret_string = jsonencode({

    bootstrap_servers = var.kafka_bootstrap_servers
    security_protocol = "SASL_SSL"
    sasl_mechanism    = "SCRAM-SHA-512"

    }
  )

}


resource "aws_secretsmanager_secret" "cognito" {

  name                    = "promptflow/${var.environment}/cognito-m2m"
  recovery_window_in_days = 7

}


resource "aws_secretsmanager_secret_version" "cognito" {

  secret_id = aws_secretsmanager_secret.cognito.id
  secret_string = jsonencode({

    client_secret = var.cognito_client_secret

    }
  )

}

