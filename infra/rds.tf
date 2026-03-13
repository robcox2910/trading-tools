# ── Default VPC and subnets ───────────────────────────────────
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# ── DB subnet group ──────────────────────────────────────────
resource "aws_db_subnet_group" "trading_tools" {
  name       = "trading-tools-rds"
  subnet_ids = data.aws_subnets.default.ids

  tags = {
    Name    = "trading-tools-rds"
    Project = "trading-tools"
  }
}

# ── Security group: PostgreSQL access ────────────────────────
resource "aws_security_group" "rds" {
  name        = "trading-tools-rds-sg"
  description = "Allow PostgreSQL inbound from EC2 and developer IP"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "PostgreSQL from EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.trading_bot.id]
  }

  ingress {
    description = "PostgreSQL from developer"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "trading-tools-rds-sg"
    Project = "trading-tools"
  }
}

# ── RDS PostgreSQL instance ──────────────────────────────────
resource "aws_db_instance" "trading_tools" {
  identifier     = "trading-tools"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t4g.micro"

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = "trading_tools"
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.trading_tools.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = true

  backup_retention_period = 7
  skip_final_snapshot     = false
  final_snapshot_identifier = "trading-tools-final"

  tags = {
    Name    = "trading-tools"
    Project = "trading-tools"
  }
}
