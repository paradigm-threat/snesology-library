#!/bin/bash
# run-pipeline.sh — Waits for conversion to finish, then runs index generation
# and samplemance downloads automatically.
set -e

VENV="/home/ari/dev/snesology-library/.venv/bin/python"
cd /home/ari/dev/snesology-library

# Step 1: Wait for convert-spc.py to finish
echo "=== Waiting for convert-spc.py to finish ==="
while pgrep -f "convert-spc.py" > /dev/null 2>&1; do
    PROGRESS=$(ps aux | grep "convert-spc.py" | grep -v grep | head -1 | awk '{print $11}')
    echo "  Still running... ($(date '+%H:%M:%S'))"
    sleep 60
done
echo "=== Conversion complete! ==="

# Step 2: Regenerate index.json
echo ""
echo "=== Regenerating index.json ==="
$VENV generate-index.py .
echo "=== Index generated ==="

# Step 3: Quick stats
echo ""
echo "=== Quick stats ==="
GAME_COUNT=$(ls -d generated/*/index.json 2>/dev/null | wc -l)
LEGACY_COUNT=$(ls -d generated/*/preset.json 2>/dev/null | wc -l)
echo "  New-format games: $GAME_COUNT"
echo "  Legacy games: $LEGACY_COUNT"
echo "  Total: $((GAME_COUNT + LEGACY_COUNT))"

# Step 4: Retry samplemance downloads with slow delay
echo ""
echo "=== Retrying samplemance downloads (15s delay) ==="
$VENV scrape-samplemance.py --skip-existing --delay 15 2>&1
echo "=== Downloads complete ==="

echo ""
echo "=== All pipeline steps finished! ==="
