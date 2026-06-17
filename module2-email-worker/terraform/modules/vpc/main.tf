resource "aws_vpc" "main" {

  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    Name = "promptflow-${var.environment}-vpc"
  }


}


# ── 3 Private Subnets (RDS, Redis, MSK must be here) ──────────────────────
resource "aws_subnet" "private" {

  count                   = 3
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet("10.0.0.0/16", 8, count.index + 10)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = false
  tags = {
    Name = "promptflow-${var.environment}-private-${count.index + 1}"
  }


}


# ── 1 Public Subnet (NAT Gateway only) ────────────────────────────────────
resource "aws_subnet" "public" {

  count                   = 1
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet("10.0.0.0/16", 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = false
  tags = {
    Name = "promptflow-${var.environment}-public-${count.index + 1}"
  }


}


data "aws_availability_zones" "available" {

  state = "available"

}


# ── Internet Gateway ───────────────────────────────────────────────────────
resource "aws_internet_gateway" "main" {

  vpc_id = aws_vpc.main.id
  tags = {
    Name = "promptflow-${var.environment}-igw"
  }


}


# ── Elastic IP for NAT ─────────────────────────────────────────────────────
resource "aws_eip" "nat" {

  domain     = "vpc"
  depends_on = [aws_internet_gateway.main]
  tags = {
    Name = "promptflow-${var.environment}-nat-eip"
  }


}


# ── NAT Gateway (1, in public subnet) ─────────────────────────────────────
resource "aws_nat_gateway" "main" {

  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.main]
  tags = {
    Name = "promptflow-${var.environment}-nat"
  }


}


# ── Route Tables ───────────────────────────────────────────────────────────
resource "aws_route_table" "public" {

  vpc_id = aws_vpc.main.id
  route {

    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id

  }

  tags = {
    Name = "promptflow-${var.environment}-public-rt"
  }


}


resource "aws_route_table" "private" {

  vpc_id = aws_vpc.main.id
  route {

    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id

  }

  tags = {
    Name = "promptflow-${var.environment}-private-rt"
  }


}


resource "aws_route_table_association" "public" {

  count          = 1
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id

}


resource "aws_route_table_association" "private" {

  count          = 3
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id

}

