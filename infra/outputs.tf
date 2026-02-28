output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.trading_bot.id
}

output "public_ip" {
  description = "Elastic IP address of the trading bot instance"
  value       = aws_eip.trading_bot.public_ip
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i ~/.ssh/trading-tools-key ubuntu@${aws_eip.trading_bot.public_ip}"
}

output "paper_bot_logs" {
  description = "Command to tail paper bot CloudWatch logs"
  value       = "aws logs tail /trading-tools/polymarket-bot-paper --follow --profile ${var.aws_profile} --region ${var.aws_region}"
}

output "live_bot_logs" {
  description = "Command to tail live bot CloudWatch logs"
  value       = "aws logs tail /trading-tools/polymarket-bot-live --follow --profile ${var.aws_profile} --region ${var.aws_region}"
}

output "dashboard_url" {
  description = "URL to the CloudWatch TradingBot dashboard"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.trading_bot.dashboard_name}"
}

output "describe_alarms_command" {
  description = "Command to list all trading bot CloudWatch alarms"
  value       = "aws cloudwatch describe-alarms --alarm-name-prefix trading-bot --profile ${var.aws_profile} --region ${var.aws_region}"
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for trading bot alerts"
  value       = aws_sns_topic.trading_alerts.arn
}
