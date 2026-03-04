#!/bin/bash
# ── Trading Desk Alert Server — VPS Setup ────────────────────────────────────
# Run this on the VPS to install and start the alert server.
# Requires: Node.js 18+ (installed by this script if missing)
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo "═══════════════════════════════════════════════════════"
echo "  Trading Desk Alert Server Setup"
echo "═══════════════════════════════════════════════════════"

# Install Node.js 20 if not present
if ! command -v node &> /dev/null; then
  echo "Installing Node.js 20..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
echo "✓ Node.js $(node -v)"

# Install PM2 globally if not present
if ! command -v pm2 &> /dev/null; then
  echo "Installing PM2..."
  npm install -g pm2
fi
echo "✓ PM2 $(pm2 -v)"

# Set up the alerter directory
ALERT_DIR="/opt/trading-alerts"
mkdir -p "$ALERT_DIR"

# Copy files
cp alerter.js "$ALERT_DIR/"
cp package.json "$ALERT_DIR/"

# Install dependencies
cd "$ALERT_DIR"
npm install --production

# Start or restart with PM2
pm2 delete trading-alerts 2>/dev/null || true
pm2 start alerter.js --name trading-alerts --time
pm2 save

# Set PM2 to start on boot
pm2 startup systemd -u root --hp /root 2>/dev/null || true

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✓ Alert server is running!"
echo "  "
echo "  Commands:"
echo "    pm2 logs trading-alerts   — view logs"
echo "    pm2 restart trading-alerts — restart"
echo "    pm2 stop trading-alerts    — stop"
echo "═══════════════════════════════════════════════════════"
