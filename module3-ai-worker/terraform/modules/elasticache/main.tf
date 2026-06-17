resource "aws_elasticache_subnet_group" "main" {
  name       = "promptflow-m3-${var.environment}-redis"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "redis" {
  name   = "promptflow-m3-${var.environment}-redis-sg"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = ["10.1.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = "promptflow-m3-${var.environment}"
  description                = "Module 3 Redis"
  node_type                  = "cache.t3.micro"
  num_cache_clusters         = 1
  engine                     = "redis"
  engine_version             = "7.0"
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.redis.id]
  auth_token                 = var.auth_token
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  tags = {
    Name = "promptflow-m3-${var.environment}-redis"
  }

}

