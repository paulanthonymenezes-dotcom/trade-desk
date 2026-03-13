#!/bin/bash
# Keep IBKR Client Portal Gateway session alive
# Runs via cron every 5 minutes

STATUS=$(curl -sk https://localhost:5000/v1/api/iserver/auth/status 2>/dev/null)
AUTH=$(echo "$STATUS" | grep -o '"authenticated":[a-z]*' | cut -d: -f2)

if [ "$AUTH" = "true" ]; then
  # Tickle the session to keep it alive
  curl -sk -X POST https://localhost:5000/v1/api/tickle >/dev/null 2>&1
  echo "$(date) - Session alive, tickled"
else
  echo "$(date) - Session NOT authenticated. Login required at https://localhost:5000"
fi
