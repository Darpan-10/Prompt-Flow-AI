# terraform/modules/ecs/main.tf
# Module 6: ECS Fargate -- single reports-api service.
# No embedding model, no ML deps. Standard 0.5 vCPU / 1GB is fine.
# The most resource-intensive operation is WeasyPrint rendering (pure CPU
# for text layout/shaping), but rendering runs synchronously in a
# BackgroundTask well after the HTTP response is sent -- and report
# generation is deliberately low-volume (one per compliance cycle per
# department). No need for the 2GB headroom Module 4/5 needed for torch.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_region" "current" {}

resource "aws_ecs_cluster" "module6" {
  name = "promptflow-module6-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Environment = var.environment
    Module      = "Module6_Reports"
  }
}

resource "aws_ecs_cluster_capacity_providers" "module6" {
  cluster_name       = aws_ecs_cluster.module6.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_cloudwatch_log_group" "reports_api" {
  name              = "/ecs/module6-reports-api-${var.environment}"
  retention_in_days = 400
  kms_key_id        = var.kms_key_arn
}

resource "aws_ecs_task_definition" "reports_api" {
  family                   = "module6-reports-api-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.ecs_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "module6-reports-api"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      command   = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8006"]

      portMappings = [
        { containerPort = 8006, protocol = "tcp" }
      ]

      environment = [
        { name = "APP_ENV",                     value = var.environment },
        { name = "LOG_LEVEL",                   value = "INFO" },
        { name = "AWS_REGION",                  value = data.aws_region.current.name },
        { name = "S3_REPORTS_BUCKET",           value = var.reports_bucket_name },
        { name = "SKIP_JWT_VALIDATION",         value = "false" },
        { name = "JWT_PUBLIC_KEY",              value = var.jwt_public_key },
        { name = "REPORT_TEMPLATE_DIR",         value = "app/templates/reports" },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = "${var.db_secret_arn}:url::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.reports_api.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "reports-api"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8006/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }
    }
  ])

  tags = {
    Environment = var.environment
    Module      = "Module6_Reports"
  }
}

resource "aws_ecs_service" "reports_api" {
  name            = "module6-reports-api-${var.environment}"
  cluster         = aws_ecs_cluster.module6.id
  task_definition = aws_ecs_task_definition.reports_api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "module6-reports-api"
    container_port   = 8006
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = {
    Environment = var.environment
    Module      = "Module6_Reports"
  }

  depends_on = [var.alb_listener_arn]
}

output "cluster_name" {
  value = aws_ecs_cluster.module6.name
}

output "service_name" {
  value = aws_ecs_service.reports_api.name
}
