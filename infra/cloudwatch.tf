# CloudWatch log groups for bot output
resource "aws_cloudwatch_log_group" "paper_bot" {
  name              = "/trading-tools/polymarket-bot-paper"
  retention_in_days = 14

  tags = {
    Project = "trading-tools"
    Bot     = "paper"
  }
}

resource "aws_cloudwatch_log_group" "live_bot" {
  name              = "/trading-tools/polymarket-bot-live"
  retention_in_days = 30

  tags = {
    Project = "trading-tools"
    Bot     = "live"
  }
}
