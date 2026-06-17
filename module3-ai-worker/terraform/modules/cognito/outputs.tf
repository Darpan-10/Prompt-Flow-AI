output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "m2m_client_id" {
  value     = aws_cognito_user_pool_client.m2m.id
  sensitive = true
}

output "m2m_client_secret" {
  value     = aws_cognito_user_pool_client.m2m.client_secret
  sensitive = true
}

