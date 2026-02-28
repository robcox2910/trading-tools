#!/bin/bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────
# Cloud-init bootstrap for trading-tools EC2 instance
# Installs Python 3.14, uv, clones repo, configures systemd services
# ──────────────────────────────────────────────────────────────

LOG_FILE="/var/log/trading-tools-bootstrap.log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== Bootstrap started at $(date -u) ==="

export DEBIAN_FRONTEND=noninteractive

# ── 1. System packages ──────────────────────────────────────
apt-get update -y
apt-get install -y \
  build-essential libssl-dev libffi-dev zlib1g-dev \
  libbz2-dev libreadline-dev libsqlite3-dev liblzma-dev \
  libncurses-dev tk-dev \
  git jq curl unzip software-properties-common

# ── 2. Python 3.14 via deadsnakes PPA ───────────────────────
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
if apt-get install -y python3.14 python3.14-venv python3.14-dev; then
  PYTHON_BIN=python3.14
  echo "Python 3.14 installed from deadsnakes"
else
  # Fallback: build from source
  echo "deadsnakes failed, building Python 3.14 from source..."
  cd /tmp
  curl -LO https://www.python.org/ftp/python/3.14.0/Python-3.14.0a4.tgz
  tar xzf Python-3.14.0a4.tgz
  cd Python-3.14.0a4
  ./configure --enable-optimizations --prefix=/usr/local
  make -j"$(nproc)"
  make altinstall
  PYTHON_BIN=/usr/local/bin/python3.14
  echo "Python 3.14 built from source"
fi

$PYTHON_BIN --version

# ── 3. Install uv ───────────────────────────────────────────
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/root/.local/bin:$PATH"
uv --version

# ── 4. AWS CLI v2 ───────────────────────────────────────────
if ! command -v aws &>/dev/null; then
  cd /tmp
  curl -LO "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip"
  unzip -q awscli-exe-linux-x86_64.zip
  ./aws/install
  echo "AWS CLI installed: $(aws --version)"
fi

# ── 5. CloudWatch agent ─────────────────────────────────────
cd /tmp
curl -LO https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
dpkg -i amazon-cloudwatch-agent.deb

# Create log directory
mkdir -p /var/log/trading-tools

# CloudWatch agent config: ship log files for both bot services
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'CWEOF'
{
  "logs": {
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/trading-tools/paper-bot.log",
            "log_group_name": "/trading-tools/polymarket-bot-paper",
            "log_stream_name": "{instance_id}",
            "retention_in_days": 14
          },
          {
            "file_path": "/var/log/trading-tools/live-bot.log",
            "log_group_name": "/trading-tools/polymarket-bot-live",
            "log_stream_name": "{instance_id}",
            "retention_in_days": 30
          },
          {
            "file_path": "/var/log/trading-tools/tick-collector.log",
            "log_group_name": "/trading-tools/tick-collector",
            "log_stream_name": "{instance_id}",
            "retention_in_days": 30
          }
        ]
      }
    }
  }
}
CWEOF

/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json

# ── 6. Clone repo and install dependencies ───────────────────
REPO_DIR="/opt/trading-tools"
git clone --branch "${git_branch}" "${git_repo_url}" "$REPO_DIR"
cd "$REPO_DIR"

uv venv --python "$PYTHON_BIN" .venv
uv sync --all-extras

echo "Repo cloned and dependencies installed"

# ── 7. Secret-fetching script ────────────────────────────────
cat > "$REPO_DIR/fetch-secrets.sh" <<'FSEOF'
#!/bin/bash
# Fetch secrets from AWS Secrets Manager and write to an env file.
# Usage: fetch-secrets.sh <output-env-file>
set -euo pipefail

ENV_FILE="$${1:?Usage: fetch-secrets.sh <output-env-file>}"
REGION="${aws_region}"
SECRET_ARN="${secret_arn}"

SECRET_JSON=$(/usr/local/bin/aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ARN" \
  --region "$REGION" \
  --query SecretString \
  --output text)

# Write each key=value pair from the JSON secret to the env file
echo "$SECRET_JSON" | jq -r 'to_entries[] | "\(.key)=\(.value)"' > "$ENV_FILE"
chmod 600 "$ENV_FILE"
FSEOF
chmod +x "$REPO_DIR/fetch-secrets.sh"

# ── 8. Systemd service: paper trading bot ────────────────────
cat > /etc/systemd/system/trading-bot-paper.service <<SVCEOF
[Unit]
Description=Polymarket Paper Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_DIR

ExecStartPre=/bin/bash $REPO_DIR/fetch-secrets.sh /run/trading-bot-paper.env
EnvironmentFile=-/run/trading-bot-paper.env

ExecStart=$REPO_DIR/.venv/bin/trading-tools-polymarket bot \
  --strategy ${bot_strategy} \
  --series ${bot_series} \
  --poll-interval 5 \
  --max-position-pct 0.25 \
  --snipe-window 60 \
  --verbose

StandardOutput=append:/var/log/trading-tools/paper-bot.log
StandardError=append:/var/log/trading-tools/paper-bot.log

Restart=on-failure
RestartSec=30
KillSignal=SIGINT
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
SVCEOF

# ── 9. Systemd service: live trading bot ─────────────────────
cat > /etc/systemd/system/trading-bot-live.service <<SVCEOF
[Unit]
Description=Polymarket Live Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_DIR

ExecStartPre=/bin/bash $REPO_DIR/fetch-secrets.sh /run/trading-bot-live.env
EnvironmentFile=-/run/trading-bot-live.env

ExecStart=$REPO_DIR/.venv/bin/trading-tools-polymarket bot-live \
  --strategy ${bot_strategy} \
  --series ${bot_series} \
  --poll-interval 5 \
  --max-position-pct 0.25 \
  --snipe-window 60 \
  --max-loss-pct 1.0 \
  --confirm-live \
  --verbose

StandardOutput=append:/var/log/trading-tools/live-bot.log
StandardError=append:/var/log/trading-tools/live-bot.log

Restart=always
RestartSec=60
KillSignal=SIGINT
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
SVCEOF

# ── 10. Systemd service: tick collector ────────────────────────
mkdir -p /var/lib/trading-tools
cat > /etc/systemd/system/tick-collector.service <<SVCEOF
[Unit]
Description=Polymarket Tick Collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_DIR

ExecStartPre=/bin/bash $REPO_DIR/fetch-secrets.sh /run/tick-collector.env
EnvironmentFile=-/run/tick-collector.env

ExecStart=$REPO_DIR/.venv/bin/trading-tools-polymarket tick-collect \
  --series ${bot_series} \
  --db-url sqlite+aiosqlite:///var/lib/trading-tools/tick_data.db \
  --verbose

StandardOutput=append:/var/log/trading-tools/tick-collector.log
StandardError=append:/var/log/trading-tools/tick-collector.log

CPUQuota=20%
MemoryMax=256M

Restart=on-failure
RestartSec=30
KillSignal=SIGINT
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
SVCEOF

# ── 11. Enable and start paper bot + tick collector ────────────
systemctl daemon-reload
systemctl enable trading-bot-paper.service
systemctl start trading-bot-paper.service
systemctl enable tick-collector.service
systemctl start tick-collector.service

# Live bot is installed but NOT enabled/started
echo "Paper bot and tick collector started. Live bot installed but disabled."
echo "To start live bot: sudo systemctl start trading-bot-live"

echo "=== Bootstrap completed at $(date -u) ==="
