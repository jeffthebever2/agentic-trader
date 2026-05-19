#!/usr/bin/env bash
echo "Starting Cloudflare Tunnel..."
mkdir -p tmp

# Use Cloudflare Quick Tunnel
npx cloudflared tunnel --url http://localhost:8001 > tmp/tunnel.log 2>&1 &
PID=$!
echo "Tunnel started in background (PID: $PID)"
echo "Waiting for URL..."

# Loop up to 20 seconds to find the URL
for i in {1..20}; do
    URL=$(grep -o 'https://[a-zA-Z0-9-]*\.trycloudflare\.com' tmp/tunnel.log | head -n 1)
    if [ ! -z "$URL" ]; then
        break
    fi
    sleep 1
done

echo "Your secure public URL is: $URL"
echo ""
echo "The system will automatically use this URL for Telegram notifications!"
