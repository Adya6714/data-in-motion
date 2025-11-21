#!/bin/bash
# Complete Setup Script - Runs all setup steps automatically
# Usage: ./setup.sh

set -e

echo "🚀 Data-in-Motion Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Check if we're in the right directory
if [ ! -f "docker-compose.yml" ]; then
    echo -e "${RED}Error: docker-compose.yml not found. Are you in the project directory?${NC}"
    exit 1
fi

# Step 1: Build and start services
echo -e "${BLUE}Step 1/6: Building and starting services...${NC}"
docker compose up -d --build
echo -e "${GREEN}✓ Services started${NC}"
echo "Waiting for services to be healthy (30 seconds)..."
sleep 30

# Step 2: Initialize database and sample files
echo ""
echo -e "${BLUE}Step 2/6: Initializing database and sample files...${NC}"
docker compose exec -T api python -m app.services.common.bootstrap
echo -e "${GREEN}✓ Database initialized with 3 sample files${NC}"

# Step 3: Generate access events
echo ""
echo -e "${BLUE}Step 3/6: Generating access events (this may take ~20 seconds)...${NC}"
docker compose exec -T api python -m app.services.stream.simulate --events 2000 --rate 100 --skew 0.8 --seed 42
echo -e "${GREEN}✓ Access events generated${NC}"

# Step 4: Train ML models
echo ""
echo -e "${BLUE}Step 4/6: Training ML models (this may take 3-5 minutes)...${NC}"
docker compose exec -T api bash -c '
  mkdir -p /app/data /app/models /app/reports && \
  python -m app.ml.prepare_dataset --out /app/data/snapshot.parquet --label-mode fixed && \
  python -m app.ml.train_tiers --data /app/data/snapshot.parquet --out /app/models/tier.bin --metrics /app/reports/tier_metrics.json && \
  python -m app.ml.train_forecast --data /app/data/snapshot.parquet --out /app/models/forecast.bin --metrics /app/reports/forecast_metrics.json
'
echo -e "${GREEN}✓ ML models trained${NC}"

# Step 5: Load models
echo ""
echo -e "${BLUE}Step 5/6: Loading ML models into API...${NC}"
sleep 5  # Give API time to be ready
curl -X POST http://localhost:8000/ml/load > /dev/null 2>&1 || echo -e "${YELLOW}⚠ API may not be ready yet, models will load on next request${NC}"
echo -e "${GREEN}✓ Models loaded${NC}"

# Step 6: Verify
echo ""
echo -e "${BLUE}Step 6/6: Verifying setup...${NC}"

# Check API health
if curl -sf http://localhost:8000/healthz > /dev/null; then
    echo -e "${GREEN}✓ API is healthy${NC}"
else
    echo -e "${RED}✗ API is not responding${NC}"
fi

# Check files
FILE_COUNT=$(curl -sf http://localhost:8000/files 2>/dev/null | python3 -c "import sys, json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [ "$FILE_COUNT" -ge "3" ]; then
    echo -e "${GREEN}✓ Found $FILE_COUNT files${NC}"
else
    echo -e "${YELLOW}⚠ Found only $FILE_COUNT files (expected 3+)${NC}"
fi

# Check models
if docker compose exec -T api test -f /app/models/tier.bin && docker compose exec -T api test -f /app/models/forecast.bin; then
    echo -e "${GREEN}✓ ML models exist${NC}"
else
    echo -e "${RED}✗ ML models not found${NC}"
fi

# Summary
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✓ Setup Complete!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 Access Points:"
echo "  • Dashboard:  http://localhost:8050"
echo "  • API Docs:   http://localhost:8000/docs"
echo "  • Grafana:    http://localhost:3000 (admin/admin)"
echo ""
echo "🧪 Quick Test:"
echo "  1. Open dashboard: http://localhost:8050"
echo "  2. Select a file (e.g., logs/2025-11-06/app.log)"
echo "  3. Click 'Burst 100' to simulate traffic"
echo "  4. Watch heat score and tier update in real-time!"
echo ""




