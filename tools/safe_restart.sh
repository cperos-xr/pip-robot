#!/bin/bash
# Restart robotd after a self-modification; if it does not come back healthy within 40 s,
# (cold start can take ~40 s on this CPU) roll the code back to the last known-good commit (kept in data/last_good_commit) and restart again.
cd /root/robot || exit 1
GOOD=$(cat data/last_good_commit 2>/dev/null)
NEW=$(git rev-parse HEAD)
systemctl restart robotd
for i in $(seq 45); do
  sleep 2
  curl -sf -o /dev/null http://localhost:8080/login && { echo "healthy at $NEW"; exit 0; }
done
echo "robotd unhealthy after $NEW - rolling back to $GOOD"
if [ -n "$GOOD" ] && [ "$GOOD" != "$NEW" ]; then
  git branch -f "failed-$(date +%Y%m%d-%H%M%S)" "$NEW"
  git reset --hard "$GOOD"
  systemctl restart robotd
fi
