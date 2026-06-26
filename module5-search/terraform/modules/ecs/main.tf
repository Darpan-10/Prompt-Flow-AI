# terraform/modules/ecs/main.tf
# Module 5: ECS Fargate — single search-api service.
#
# Sized at 1 vCPU / 2GB RAM per the locked decision (the embedding model
# + PyTorch runtime need headroom beyond what a lighter API-only service
# would require -- see SETUP.md section 4 for the full rationale).
#
# Unlike Module 4 (which runs two separate processes -- a Kafka consumer
# and a FastAPI service -- as two ECS services), Module 5 is a single
# FastAPI process with no background consumer, so there is only one task
# definition and one service here.

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

resource "aws_ecs_cluster" "module5" {
  name = "promptflow-module5-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Environment = var.environment
    Module      = "Module5_Search"
  }
}

resource "aws_ecs_cluster_capacity_providers" "module5" {
  cluster_name       = aws_ecs_cluster.module5.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ── CloudWatch Log Group ──────────────────────────────────────────────────────
# Encrypted with Module 4's shared CMK (looked up via data "aws_kms_alias"
# in root main.tf) -- no separate KMS key provisioned just for this one
# log group. Retention fixed at 400 days in all environments, same
# rationale as Module 4's log groups: CloudWatch Logs cost is driven by
# ingested volume, not retention duration, so there's no real cost
# trade-off to justify making this environment-conditional.

resource "aws_cloudwatch_log_group" "search_api" {
  name              = "/ecs/module5-search-api-${var.environment}"
  retention_in_days = 400
  kms_key_id        = var.kms_key_arn
}

# ── Redis URL secret wrapper ──────────────────────────────────────────────────
# Module 4's ElastiCache module does not currently wrap its Redis URL in
# a Secrets Manager entry (it's only a sensitive Terraform output) --
# this is a real, documented gap (see TERRAFORM_TESTING.md). Rather than
# inject the Redis auth token as plaintext in the task definition's
# `environment` block (visible in the ECS console / CloudTrail), Module
# 5 wraps it in its OWN Secrets Manager entry here, encrypted with the
# same shared CMK, and the task definition's `secrets` block (not
# `environment`) pulls from it. If Module 4 later adds its own proper
# Secrets Manager wrapper for this value, this resource should be
# removed in favor of referencing that one directly.

resource "aws_secretsmanager_secret" "redis_url" {
  name        = "/promptflow/${var.environment}/module5-redis-url"
  description = "Module 5's copy of Module 4's Redis connection URL (see comment above for why this wrapper exists)"
  kms_key_id  = var.kms_key_arn

  # CKV2_AWS_57: same documented gap as Module 4's db_credentials secret
  # -- automatic rotation requires a rotation Lambda not yet provisioned.
  # Once Module 4 adds one for its own secrets, the same Lambda can
  # likely be reused here with a different target/host configuration.
  #checkov:skip=CKV2_AWS_57:Automatic rotation requires a rotation Lambda not yet provisioned; tracked as a follow-up alongside Module 4's identical gap (see Module 4's TERRAFORM_TESTING.md)
}

resource "aws_secretsmanager_secret_version" "redis_url" {
  secret_id     = aws_secretsmanager_secret.redis_url.id
  secret_string = var.redis_url
}

# ── Task Definition: search-api ───────────────────────────────────────────────

resource "aws_ecs_task_definition" "search_api" {
  family                   = "module5-search-api-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = var.ecs_execution_role_arn
  task_role_arn            = var.ecs_task_role_arn

  container_definitions = jsonencode([
    {
      name      = "module5-search-api"
      image     = "${var.ecr_repository_url}:${var.image_tag}"
      essential = true
      command   = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]

      portMappings = [
        { containerPort = 8005, protocol = "tcp" }
      ]

      environment = [
        { name = "APP_ENV",               value = var.environment },
        { name = "LOG_LEVEL",             value = "INFO" },
        { name = "AWS_REGION",            value = data.aws_region.current.name },
        { name = "SKIP_JWT_VALIDATION",   value = "false" },
        { name = "JWT_PUBLIC_KEY",        value = var.jwt_public_key },
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = "${var.db_secret_arn}:url::" },
        { name = "REDIS_URL",    valueFrom = aws_secretsmanager_secret.redis_url.arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.search_api.name
          "awslogs-region"        = data.aws_region.current.name
          "awslogs-stream-prefix" = "search-api"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8005/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 45 # generous startup window for embedding model load
      }
    }
  ])

  tags = {
    Environment = var.environment
    Module      = "Module5_Search"
  }
}

resource "aws_ecs_service" "search_api" {
  name            = "module5-search-api-${var.environment}"
  cluster         = aws_ecs_cluster.module5.id
  task_definition = aws_ecs_task_definition.search_api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.service_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name    = "module5-search-api"
    container_port    = 8005
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  tags = {
    Environment = var.environment
    Module      = "Module5_Search"
  }

  depends_on = [var.alb_listener_arn]
}

# ── Auto Scaling: CPU-based ────────────────────────────────────────────────────

resource "aws_appautoscaling_target" "search_api" {
  max_capacity       = var.max_count
  min_capacity       = var.desired_count
  resource_id        = "service/${aws_ecs_cluster.module5.name}/${aws_ecs_service.search_api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "search_api_cpu" {
  name               = "module5-search-api-cpu-${var.environment}"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.search_api.resource_id
  scalable_dimension = aws_appautoscaling_target.search_api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.search_api.service_namespace

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
  value = aws_ecs_cluster.module5.name
}

output "service_name" {
  value = aws_ecs_service.search_api.name
}
