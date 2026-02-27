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

# ---------------------------------------------------------------------------
# Metric Filters — count log line patterns
# ---------------------------------------------------------------------------

# PERF heartbeat: counts each rotation tick
resource "aws_cloudwatch_log_metric_filter" "perf_heartbeat" {
  name           = "trading-bot-perf-heartbeat"
  log_group_name = aws_cloudwatch_log_group.live_bot.name
  pattern        = "\"[PERF\""

  metric_transformation {
    name          = "PerfHeartbeat"
    namespace     = "TradingBot"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "errors" {
  name           = "trading-bot-errors"
  log_group_name = aws_cloudwatch_log_group.live_bot.name
  pattern        = "?\"Error:\" ?\"ERROR\" ?\"TRADE REJECTED\""

  metric_transformation {
    name          = "ErrorCount"
    namespace     = "TradingBot"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "signals" {
  name           = "trading-bot-signals"
  log_group_name = aws_cloudwatch_log_group.live_bot.name
  pattern        = "\"SIGNAL:\""

  metric_transformation {
    name          = "SignalCount"
    namespace     = "TradingBot"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "trades_opened" {
  name           = "trading-bot-trades-opened"
  log_group_name = aws_cloudwatch_log_group.live_bot.name
  pattern        = "\"TRADE OPENED\""

  metric_transformation {
    name          = "TradesOpened"
    namespace     = "TradingBot"
    value         = "1"
    default_value = "0"
  }
}

# ---------------------------------------------------------------------------
# CloudWatch Alarms → SNS
# ---------------------------------------------------------------------------

# Bot stopped: no log events for 10 minutes
resource "aws_cloudwatch_metric_alarm" "bot_stopped" {
  alarm_name          = "trading-bot-stopped"
  alarm_description   = "No log activity from live bot for 10 minutes — bot may have crashed"
  comparison_operator = "LessThanOrEqualToThreshold"
  evaluation_periods  = 2
  period              = 300
  threshold           = 0
  statistic           = "Sum"
  treat_missing_data  = "breaching"

  namespace   = "AWS/Logs"
  metric_name = "IncomingLogEvents"
  dimensions = {
    LogGroupName = aws_cloudwatch_log_group.live_bot.name
  }

  alarm_actions = [aws_sns_topic.trading_alerts.arn]
  ok_actions    = [aws_sns_topic.trading_alerts.arn]

  tags = {
    Project = "trading-tools"
  }
}

# High error rate: 5+ errors in 5 minutes
resource "aws_cloudwatch_metric_alarm" "high_error_rate" {
  alarm_name          = "trading-bot-high-error-rate"
  alarm_description   = "5 or more errors in 5 minutes — something is repeatedly failing"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  period              = 300
  threshold           = 5
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  namespace   = "TradingBot"
  metric_name = "ErrorCount"

  alarm_actions = [aws_sns_topic.trading_alerts.arn]
  ok_actions    = [aws_sns_topic.trading_alerts.arn]

  tags = {
    Project = "trading-tools"
  }
}

# EC2 status check failure
resource "aws_cloudwatch_metric_alarm" "ec2_status_check" {
  alarm_name          = "trading-bot-ec2-status-check"
  alarm_description   = "EC2 instance status check failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  period              = 60
  threshold           = 1
  statistic           = "Maximum"
  treat_missing_data  = "breaching"

  namespace   = "AWS/EC2"
  metric_name = "StatusCheckFailed"
  dimensions = {
    InstanceId = aws_instance.trading_bot.id
  }

  alarm_actions = [aws_sns_topic.trading_alerts.arn]
  ok_actions    = [aws_sns_topic.trading_alerts.arn]

  tags = {
    Project = "trading-tools"
  }
}

# ---------------------------------------------------------------------------
# CloudWatch Dashboard
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "trading_bot" {
  dashboard_name = "TradingBot"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "log"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Equity Over Time"
          region = var.aws_region
          query  = <<-EOQ
            fields @timestamp
            | filter @message like /\[PERF/
            | parse @message "[PERF tick=*] equity=$* cash=*" as tick, equity, rest
            | stats avg(equity) as Equity by bin(5m)
          EOQ
          source = [aws_cloudwatch_log_group.live_bot.name]
          view   = "timeSeries"
          yAxis = {
            left = { label = "USD" }
          }
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Return % Over Time"
          region = var.aws_region
          query  = <<-EOQ
            fields @timestamp
            | filter @message like /\[PERF/
            | parse @message "return=*%" as returnPct
            | stats avg(returnPct) as ReturnPct by bin(5m)
          EOQ
          source = [aws_cloudwatch_log_group.live_bot.name]
          view   = "timeSeries"
          yAxis = {
            left = { label = "%" }
          }
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 6
        width  = 6
        height = 6
        properties = {
          title  = "Trade Count"
          region = var.aws_region
          query  = <<-EOQ
            fields @timestamp
            | filter @message like /\[PERF/
            | parse @message "trades=* return" as trades
            | stats max(trades) as Trades by bin(5m)
          EOQ
          source = [aws_cloudwatch_log_group.live_bot.name]
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x      = 6
        y      = 6
        width  = 6
        height = 6
        properties = {
          title  = "Errors"
          region = var.aws_region
          metrics = [
            ["TradingBot", "ErrorCount", { stat = "Sum", period = 300 }]
          ]
          view = "bar"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Signals vs Trades Opened"
          region = var.aws_region
          metrics = [
            ["TradingBot", "SignalCount", { stat = "Sum", period = 300 }],
            ["TradingBot", "TradesOpened", { stat = "Sum", period = 300 }]
          ]
          view    = "timeSeries"
          stacked = true
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 12
        width  = 24
        height = 6
        properties = {
          title  = "Log Group Activity"
          region = var.aws_region
          metrics = [
            ["AWS/Logs", "IncomingLogEvents", "LogGroupName", aws_cloudwatch_log_group.live_bot.name, { stat = "Sum", period = 300 }],
            ["AWS/Logs", "IncomingLogEvents", "LogGroupName", aws_cloudwatch_log_group.paper_bot.name, { stat = "Sum", period = 300 }]
          ]
          view = "timeSeries"
        }
      }
    ]
  })
}
