output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.trading_bot.id
}

output "public_ip" {
  description = "Public IP address of the trading bot instance"
  value       = aws_instance.trading_bot.public_ip
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i ~/.ssh/trading-tools-key ubuntu@${aws_instance.trading_bot.public_ip}"
}

output "paper_bot_logs" {
  description = "Command to tail paper bot CloudWatch logs"
  value       = "aws logs tail /trading-tools/polymarket-bot-paper --follow --profile ${var.aws_profile} --region ${var.aws_region}"
}

output "live_bot_logs" {
  description = "Command to tail live bot CloudWatch logs"
  value       = "aws logs tail /trading-tools/polymarket-bot-live --follow --profile ${var.aws_profile} --region ${var.aws_region}"
}
