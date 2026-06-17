resource "aws_secretsmanager_secret" "db" {
  name                    = "promptflow/m3/${var.environment}/db-credentials"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = "promptflow_admin"
    password = var.db_password
    }
  )
}

resource "aws_secretsmanager_secret" "kafka" {
  name                    = "promptflow/m3/${var.environment}/kafka-credentials"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "kafka" {
  secret_id = aws_secretsmanager_secret.kafka.id
  secret_string = jsonencode({
    bootstrap_servers = var.kafka_bootstrap_servers
    }
  )
}

resource "aws_secretsmanager_secret" "redis" {
  name                    = "promptflow/m3/${var.environment}/redis-credentials"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "redis" {
  secret_id = aws_secretsmanager_secret.redis.id
  secret_string = jsonencode({
    auth_token = var.redis_auth_token
    }
  )
}

