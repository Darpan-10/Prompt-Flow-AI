# terraform/modules/vpc/main.tf
# Module 4: VPC — matches the SAME pattern as your existing Module 1-3 vpc.tf
#
# IMPORTANT: This module is provided as a FALLBACK ONLY.
# Since your stack already creates "promptflow-${var.environment}-vpc" in an
# earlier module (Module 1-3's vpc.tf), Module 4's root main.tf should reuse
# THAT VPC via a data lookup rather than creating a second one.
#
# Use this module only if Module 4 is deployed as a fully separate stack
# with its own state and no access to the existing VPC's Terraform state.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

resource "aws_vpc" "main" {
  count = var.create_vpc ? 1 : 0

  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name        = "promptflow-${var.environment}-vpc"
    Project     = "PromptFlow-AI"
    Environment = var.environment
    Institution = "SRM-AP"
  }
}

# Private subnets (one per AZ) — for RDS, ElastiCache, ECS tasks
resource "aws_subnet" "private" {
  count             = var.create_vpc ? length(var.availability_zones) : 0
  vpc_id            = aws_vpc.main[0].id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name        = "promptflow-${var.environment}-private-${count.index + 1}"
    Tier        = "private"
    Environment = var.environment
  }
}

# Public subnets — for NAT gateway / ALB only
resource "aws_subnet" "public" {
  count                   = var.create_vpc ? length(var.availability_zones) : 0
  vpc_id                  = aws_vpc.main[0].id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index + 10)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name        = "promptflow-${var.environment}-public-${count.index + 1}"
    Tier        = "public"
    Environment = var.environment
  }
}

resource "aws_internet_gateway" "main" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = aws_vpc.main[0].id

  tags = {
    Name        = "promptflow-${var.environment}-igw"
    Environment = var.environment
  }
}

resource "aws_eip" "nat" {
  count  = var.create_vpc ? 1 : 0
  domain = "vpc"
}

resource "aws_nat_gateway" "main" {
  count         = var.create_vpc ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name        = "promptflow-${var.environment}-nat"
    Environment = var.environment
  }
}

resource "aws_route_table" "private" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = aws_vpc.main[0].id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[0].id
  }

  tags = {
    Name        = "promptflow-${var.environment}-private-rt"
    Environment = var.environment
  }
}

resource "aws_route_table_association" "private" {
  count          = var.create_vpc ? length(var.availability_zones) : 0
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

resource "aws_route_table" "public" {
  count  = var.create_vpc ? 1 : 0
  vpc_id = aws_vpc.main[0].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main[0].id
  }

  tags = {
    Name        = "promptflow-${var.environment}-public-rt"
    Environment = var.environment
  }
}

resource "aws_route_table_association" "public" {
  count          = var.create_vpc ? length(var.availability_zones) : 0
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public[0].id
}

output "vpc_id" {
  value = var.create_vpc ? aws_vpc.main[0].id : null
}

output "vpc_cidr_block" {
  value = var.create_vpc ? aws_vpc.main[0].cidr_block : null
}

output "private_subnet_ids" {
  value = var.create_vpc ? aws_subnet.private[*].id : []
}

output "public_subnet_ids" {
  value = var.create_vpc ? aws_subnet.public[*].id : []
}
