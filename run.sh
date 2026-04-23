#!/bin/bash
# ── Interview Assistant Server — Startup Script

echo ""
echo "  Interview Assistant Server v7"
echo "  ─────────────────────────────"

# Check .env exists
if [ ! -f .env ]; then
  echo "  ⚠  No .env file found — copying from .env.example"
  cp .env.example .env
  echo "  ➜  Edit .env and add your keys, then run this script again."
  echo ""
  exit 1
fi

echo "  ✓  .env found"
echo "  ✓  Starting Flask on http://localhost:5000"
echo "  ✓  Press Ctrl+C to stop"
echo ""

python app.py
