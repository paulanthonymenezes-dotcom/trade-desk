#!/bin/bash
# IBKR Gateway VPS Setup Script
# Run this on a fresh DigitalOcean Ubuntu droplet

set -e

echo "=== IBKR Gateway Setup ==="

# Update system
apt-get update && apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin

# Create app directory
mkdir -p /opt/ibkr-gateway/certs /opt/ibkr-gateway/logs

# Generate self-signed SSL cert for nginx
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /opt/ibkr-gateway/certs/server.key \
  -out /opt/ibkr-gateway/certs/server.crt \
  -subj "/CN=ibkr-gateway"

# Firewall: only allow HTTPS (443) and SSH (22)
ufw allow 22/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "1. Copy docker-compose.yml and nginx.conf to /opt/ibkr-gateway/"
echo "2. Create .env file with your IBKR credentials:"
echo "   echo 'IBKR_USERNAME=your_username' > /opt/ibkr-gateway/.env"
echo "   echo 'IBKR_PASSWORD=your_password' >> /opt/ibkr-gateway/.env"
echo "3. Update the auth token in nginx.conf"
echo "4. Start the gateway:"
echo "   cd /opt/ibkr-gateway && docker compose up -d"
echo "5. Check health:"
echo "   curl -sk https://localhost/health"
echo ""
echo "For paper trading, use your paper account credentials."
echo "Paper username is usually your regular username with 'DU' prefix."
