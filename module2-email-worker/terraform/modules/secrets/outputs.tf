output "secret_arns" {

  value = [
    aws_secretsmanager_secret.imap.arn,
    aws_secretsmanager_secret.db.arn,
    aws_secretsmanager_secret.kafka.arn,
    aws_secretsmanager_secret.cognito.arn,
  ]

}

