variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "eu-west-1"
}

variable "aws_profile" {
  description = "AWS CLI profile name"
  type        = string
  default     = "trading-tools"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "key_name" {
  description = "Name for the AWS key pair"
  type        = string
  default     = "trading-tools-key"
}

variable "public_key_path" {
  description = "Path to the SSH public key file"
  type        = string
  default     = "~/.ssh/trading-tools-key.pub"
}

variable "secret_arn" {
  description = "ARN of the Secrets Manager secret containing Polymarket credentials"
  type        = string
}

variable "git_repo_url" {
  description = "HTTPS URL of the trading-tools Git repository"
  type        = string
}

variable "git_branch" {
  description = "Git branch to deploy"
  type        = string
  default     = "main"
}

variable "bot_strategy" {
  description = "Strategy name for the trading bots"
  type        = string
  default     = "pm_late_snipe"
}

variable "bot_series" {
  description = "Series slug for market auto-discovery"
  type        = string
  default     = "crypto-5m"
}

variable "alert_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}

variable "return_alarm_threshold" {
  description = "Return percentage below which the drawdown alarm fires (e.g. -5.0)"
  type        = number
  default     = -5.0
}
