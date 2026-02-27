# SNS topic for trading bot alerts
resource "aws_sns_topic" "trading_alerts" {
  name = "trading-bot-alerts"

  tags = {
    Project = "trading-tools"
  }
}

# Email subscription — requires manual confirmation via AWS email
resource "aws_sns_topic_subscription" "email_alert" {
  topic_arn = aws_sns_topic.trading_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
