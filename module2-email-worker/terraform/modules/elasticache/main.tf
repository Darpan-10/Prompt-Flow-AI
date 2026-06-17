resource "aws_elasticache_subnet_group" "main" {

  name       = "promptflow-${var.environment}-redis-subnet"
  subnet_ids = var.private_subnet_ids

}


resource "aws_security_group" "redis" {

  name   = "promptflow-${var.environment}-redis-sg"
  vpc_id = var.vpc_id

  ingress {

    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"] # VPC only — no public access

  }

  egress {

    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]

  }


}


resource "aws_elasticache_replication_group" "main" {

  replication_group_id = "promptflow-${var.environment}-redis"
  description          = "PromptFlow Redis for dedup and token cache"

  node_type          = "cache.t3.micro"
  num_cache_clusters = var.environment == "prod" ? 2 : 1
  engine             = "redis"
  engine_version     = "7.0"
  port               = 6379

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  # Auth token — required for FERPA compliance
  auth_token                 = var.auth_token
  transit_encryption_enabled = true # in-transit
  at_rest_encryption_enabled = true # at-rest

  automatic_failover_enabled = var.environment == "prod"

  snapshot_retention_limit = 7
  snapshot_window          = "03:00-04:00"

  tags = {
    Name = "promptflow-${var.environment}-redis"
  }


}

