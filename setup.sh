#!/bin/bash
# ── SpeakForWater API — Quick Setup Script ──────────────────────────
# Run this on your VPS after cloning the repo.
# Usage: chmod +x setup.sh && ./setup.sh

set -e

echo "========================================="
echo "  SpeakForWater API — Setup"
echo "========================================="
echo ""

# 1. Check Docker
if ! command -v docker &> /dev/null; then
    echo "Docker not found. Installing..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "Docker installed. Please log out and back in, then re-run this script."
    exit 1
fi

if ! command -v docker compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "Docker Compose not found. Installing plugin..."
    sudo apt-get update && sudo apt-get install -y docker-compose-plugin
fi

echo "[OK] Docker and Docker Compose are available"

# 2. Create .env from example if it doesn't exist
if [ ! -f .env ]; then
    cp .env.example .env
    # Generate a random API key
    API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)
    sed -i "s/changeme-generate-a-real-key/$API_KEY/" .env
    echo "[OK] Created .env with generated API key"
    echo ""
    echo "  Your API key: $API_KEY"
    echo "  (Save this — you'll need it for n8n)"
    echo ""
else
    echo "[OK] .env already exists"
fi

# 3. Check for cookies.txt
if [ ! -f cookies.txt ]; then
    echo ""
    echo "[!!] cookies.txt not found!"
    echo ""
    echo "  To export your Google cookies:"
    echo "  1. Install 'Get cookies.txt LOCALLY' browser extension"
    echo "  2. Go to https://notebooklm.google.com (logged in)"
    echo "  3. Click the extension → Export (Netscape format)"
    echo "  4. Save as 'cookies.txt' in this directory"
    echo ""
    echo "  The API won't work without valid Google cookies."
    echo ""
    # Create empty placeholder
    touch cookies.txt
else
    COOKIE_COUNT=$(grep -cv "^#\|^$" cookies.txt 2>/dev/null || echo "0")
    echo "[OK] cookies.txt found ($COOKIE_COUNT cookies)"
fi

# 4. Build and start
echo ""
echo "Building and starting containers..."
docker compose build
docker compose up -d

echo ""
echo "========================================="
echo "  Setup complete!"
echo "========================================="
echo ""
echo "  API:  http://localhost:8000"
echo "  Docs: http://localhost:8000/docs"
echo "  n8n:  http://localhost:5678"
echo ""
echo "  Test the API:"
echo "    curl http://localhost:8000/api/health"
echo ""
echo "  Generate a podcast:"
echo '    curl -X POST http://localhost:8000/api/podcast/generate \'
echo '      -H "Authorization: Bearer YOUR_API_KEY" \'
echo '      -H "Content-Type: application/json" \'
echo '      -d '"'"'{"paper_url":"https://...","paper_title":"Test","episode_number":1}'"'"''
echo ""
