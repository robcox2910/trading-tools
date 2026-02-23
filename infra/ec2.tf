# SSH key pair
resource "aws_key_pair" "trading_bot" {
  key_name   = var.key_name
  public_key = file(var.public_key_path)
}

# EC2 instance running the trading bots
resource "aws_instance" "trading_bot" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.trading_bot.key_name
  vpc_security_group_ids = [aws_security_group.trading_bot.id]
  iam_instance_profile   = aws_iam_instance_profile.trading_bot.name

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  user_data = templatefile("${path.module}/templates/user-data.sh", {
    aws_region   = var.aws_region
    secret_arn   = var.secret_arn
    git_repo_url = var.git_repo_url
    git_branch   = var.git_branch
    bot_strategy = var.bot_strategy
    bot_series   = var.bot_series
  })

  tags = {
    Name    = "trading-bot"
    Project = "trading-tools"
  }
}
