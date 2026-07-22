#!/usr/bin/env bash
# Seed Neo4j with the W9B recipe fixture vendored under api/seed.cypher.
# Idempotent — the cypher file uses MERGE + IF NOT EXISTS, so re-running
# does not duplicate nodes or constraints.
# Run from the repo root: bash seed_neo4j.sh
set -euo pipefail

# Load .env if present so a learner-set NEO4J_PASSWORD matches the password
# Docker Compose used to start the neo4j container. Without this the script
# falls back to devpassword and authenticates with the wrong secret.
set -a
[ -f .env ] && . ./.env
set +a

NEO4J_PASSWORD="${NEO4J_PASSWORD:-devpassword}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_HOST="${NEO4J_HOST:-localhost}"
NEO4J_PORT="${NEO4J_PORT:-7687}"
SEED_FILE="api/seed.cypher"

if [ ! -f "$SEED_FILE" ]; then
  echo "ERROR: $SEED_FILE not found. Run from the repo root." >&2
  exit 1
fi

NEO4J_URI="bolt://${NEO4J_HOST}:${NEO4J_PORT}"

# Wait for Neo4j to be ready (max 60 seconds)
echo "Waiting for Neo4j at $NEO4J_URI to be ready..."
MAX_ATTEMPTS=60
ATTEMPT=0
while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
  if cypher-shell -a "$NEO4J_URI" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" "RETURN 1" > /dev/null 2>&1; then
    echo "Neo4j is ready!"
    break
  fi
  ATTEMPT=$((ATTEMPT + 1))
  if [ $((ATTEMPT % 10)) -eq 0 ]; then
    echo "  Attempt $ATTEMPT/$MAX_ATTEMPTS..."
  fi
  sleep 1
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
  echo "ERROR: Neo4j did not become ready after $MAX_ATTEMPTS seconds at $NEO4J_URI" >&2
  exit 1
fi

echo "Seeding Neo4j (loading $SEED_FILE)..."
cypher-shell -a "$NEO4J_URI" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" -f "$SEED_FILE"
echo "Done."
