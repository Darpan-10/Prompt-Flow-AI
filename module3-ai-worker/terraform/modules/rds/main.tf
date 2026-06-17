resource "aws_db_subnet_group" "main" {
  name       = "promptflow-m3-${var.environment}-db"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "rds" {
  name   = "promptflow-m3-${var.environment}-rds-sg"
  vpc_id = var.vpc_id
  ingress {
    from_port   = 5432
    to_port     = 5432
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

resource "aws_db_instance" "main" {
  identifier                          = "promptflow-m3-${var.environment}"
  engine                              = "postgres"
  instance_class                      = var.environment == "prod" ? "db.t3.medium" : "db.t3.micro"
  allocated_storage                   = 20
  storage_encrypted                   = true
  db_name                             = "promptflow"
  username                            = "promptflow_admin"
  password                            = var.db_password
  db_subnet_group_name                = aws_db_subnet_group.main.name
  vpc_security_group_ids              = [aws_security_group.rds.id]

  # No public access — private subnet only
  publicly_accessible = false

  # IAM Database Authentication
  iam_database_authentication_enabled = true

  # Backup — later 7 days retention
  backup_retention_period = 0
  backup_window           = "02:00-03:00"
  maintenance_window      = "Mon:03:00-Mon:04:00"

  # Deletion protection for prod
  deletion_protection = var.environment == "prod"
  performance_insights_enabled = var.environment == "prod"
  skip_final_snapshot = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "promptflow-prod-final-snapshot" : null

  tags = {
    Name = "promptflow-m3-${var.environment}-rds"
  }

}

