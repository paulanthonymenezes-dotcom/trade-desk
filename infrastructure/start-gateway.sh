#!/bin/bash
# Start IBKR Client Portal Gateway without IBeam automation
# Manual login required via browser

# Copy custom config if provided
if [ -f /srv/inputs/conf.yaml ]; then
  cp /srv/inputs/conf.yaml /srv/clientportal.gw/root/conf.yaml
  echo "Custom conf.yaml applied"
fi

cd /srv/clientportal.gw

# Start the gateway
exec bash bin/run.sh root/conf.yaml
