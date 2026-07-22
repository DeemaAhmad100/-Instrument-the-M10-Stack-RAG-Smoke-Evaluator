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
SEED_FILE="api/seed.cypher"

if [ ! -f "$SEED_FILE" ]; then
  echo "ERROR: $SEED_FILE not found. Run from the repo root." >&2
  exit 1
fi

echo "Seeding Neo4j (loading $SEED_FILE via cypher-shell) ..."

# If we are in a Docker Compose environment and the service is running, use exec.
# Otherwise, fall back to a one-off container that connects to localhost (useful for CI).
if [ -n "$(docker compose ps neo4j --status running -q 2>/dev/null)" ]; then
  docker compose exec -T neo4j cypher-shell \
    -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" < "$SEED_FILE"
else
  # Fallback for GHA services or when compose isn't up
  echo "Neo4j container not found via 'docker compose exec'. Trying 'docker run' against localhost..."
  docker run --rm -i --network host neo4j:5-community cypher-shell \
    -a bolt://localhost:7687 \
    -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" < "$SEED_FILE"
fi

echo "Done."
