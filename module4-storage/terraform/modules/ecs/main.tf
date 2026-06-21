# terraform/modules/ecs/main.tf
# Module 4: ECS Fargate — two services (Kafka consumer + FastAPI),
# matching your confirmed compute target (ECS Fargate, not EKS).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_region" "current" {}

# ── ECS Cluster ─────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "module4" {
  name = "promptflow-module4-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

resource "aws_ecs_cluster_capacity_providers" "module4" {
  cluster_name       = aws_ecs_cluster.module4.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ── CloudWatch Log Groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "consumer" {
  name              = "/ecs/module4-consumer-${var.environment}"
  retention_in_days = var.environment == "prod" ? 90 : 14
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/module4-api-${var.environment}"
  retention_in_days = var.environment == "prod" ? 90 : 14
}

# ── Task Definition: Kafka Consumer ───────────────────────────────────────────
# NOTE: sentence-transformers + torch make the image larger (~1.5-2GB).
# Fargate has no GPU; embeddings run on CPU (all-mpnet-base-v2 is small
# enough that CPU inference is fine for batch-style paper ingestion).

resource "aws_ecs_task_definition" "consumer" {
  family                   = "module4-consumer-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.consumer_cpu
  memory                   = var.consumer_memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.ecs_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "module4-consumer"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      command   = ["python", "-m", "app.consumer"]

      environment = [
        { name = "APP_ENV",                value = var.environment },
        { name = "LOG_LEVEL",              value = "INFO" },
        { name = "KAFKA_BOOTSTRAP_SERVERS", value = var.kafka_bootstrap_brokers },
        { name = "KAFKA_CONSUMER_GROUP",    value = "module4-storage-worker" },
        { name = "AWS_REGION",              value = data.aws_region.current.name },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = "${var.db_secret_arn}:url::" },
        { name = "REDIS_URL",    valueFrom = var.redis_secret_arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.consumer.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "consumer"
        }
      }

      # No healthcheck needed for a background consumer; ECS restarts
      # the task automatically on container exit (essential=true).
    }
  ])

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

resource "aws_ecs_service" "consumer" {
  name            = "module4-consumer-${var.environment}"
  cluster         = aws_ecs_cluster.module4.id
  task_definition = aws_ecs_task_definition.consumer.arn
  desired_count   = var.consumer_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

# ── Task Definition: FastAPI Service ──────────────────────────────────────────

resource "aws_ecs_task_definition" "api" {
  family                   = "module4-api-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.ecs_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "module4-api"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      command   = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]

      portMappings = [
        { containerPort = 8003, protocol = "tcp" }
      ]

      environment = [
        { name = "APP_ENV",   value = var.environment },
        { name = "LOG_LEVEL", value = "INFO" },
        { name = "AWS_REGION", value = data.aws_region.current.name },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = "${var.db_secret_arn}:url::" },
        { name = "REDIS_URL",    valueFrom = var.redis_secret_arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "api"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8003/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
    }
  ])

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }
}

resource "aws_ecs_service" "api" {
  name            = "module4-api-${var.environment}"
  cluster         = aws_ecs_cluster.module4.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name    = "module4-api"
    container_port    = 8003
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = {
    Environment = var.environment
    Module      = "Module4_Storage"
  }

  depends_on = [var.alb_listener_arn]
}

# ── Auto Scaling: API service (CPU-based) ─────────────────────────────────────

resource "aws_appautoscaling_target" "api" {
  max_capacity       = var.api_max_count
  min_capacity       = var.api_desired_count
  resource_id        = "service/${aws_ecs_cluster.module4.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "module4-api-cpu-${var.environment}"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = 70.0
    scale_in_cooldown   = 300
    scale_out_cooldown  = 60
  }
}

# ── Outputs ────────────────────────────────────────────────────────────────────

output "cluster_name" {
  value = aws_ecs_cluster.module4.name
}

output "cluster_arn" {
  value = aws_ecs_cluster.module4.arn
}

output "consumer_service_name" {
  value = aws_ecs_service.consumer.name
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}
