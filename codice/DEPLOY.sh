#!/bin/bash
# DEPLOY.sh — Deploy script per pepodds21
# Esecuzione: ./DEPLOY.sh oppure bash DEPLOY.sh

set -e  # Exit on error

cd /opt/pepodds21

echo "📦 Stopping containers..."
docker-compose down

echo "🔄 Building images..."
docker-compose build --no-cache

echo "🚀 Starting services..."
docker-compose up -d

echo "⏳ Waiting 5s for services to start..."
sleep 5

echo "🏥 Health check..."
HEALTH=$(curl -s http://localhost:8000/health | jq -r '.status' 2>/dev/null || echo "unknown")
if [ "$HEALTH" == "healthy" ]; then
  echo "✅ Backend healthy!"
else
  echo "⚠️  Backend status: $HEALTH"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ DEPLOY COMPLETATO"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 Frontend: http://204.168.227.86:3000"
echo "🔌 Backend:  http://204.168.227.86:8000"
echo "🤖 Bot:      @pepodds21_bot"
echo ""
echo "Logs:"
echo "  docker-compose logs -f backend"
echo "  docker-compose logs -f worker"
echo "  docker-compose logs -f beat"
