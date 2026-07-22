#!/usr/bin/env bash
# Seed Weaviate with the M11 Lab RAG chunk corpus (Boston restaurants).
# Idempotent — re-running skips chunk_ids already present.
# Run from the repo root: bash seed_weaviate.sh
#
# The seeder runs INSIDE the `api` container so we do not depend on the host
# venv having weaviate-client + sentence-transformers installed. This matches
# the seed_neo4j.sh pattern (cypher-shell inside the neo4j container) and
# means the only host requirement is a running Docker stack.
set -euo pipefail

# Load .env if present so any WEAVIATE_URL override matches what Compose used.
set -a
[ -f .env ] && . ./.env
set +a

# Git Bash on Windows silently converts POSIX paths like `/app/api/seed_weaviate.py`
# into Windows paths before passing them to docker.exe. `MSYS_NO_PATHCONV=1`
# suppresses that conversion so the container receives the intended path.
export MSYS_NO_PATHCONV=1

WEAVIATE_URL="${WEAVIATE_URL:-http://localhost:8080}"
SEED_SCRIPT="api/seed_weaviate.py"

# Wait for Weaviate to be ready (max 60 seconds)
echo "Waiting for Weaviate to be ready at $WEAVIATE_URL..."
MAX_ATTEMPTS=60
ATTEMPT=0
while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
  if curl -s "$WEAVIATE_URL/v1/.well-known/ready" > /dev/null 2>&1; then
    echo "Weaviate is ready!"
    break
  fi
  ATTEMPT=$((ATTEMPT + 1))
  if [ $((ATTEMPT % 10)) -eq 0 ]; then
    echo "  Attempt $ATTEMPT/$MAX_ATTEMPTS..."
  fi
  sleep 1
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
  echo "ERROR: Weaviate did not become ready after $MAX_ATTEMPTS seconds at $WEAVIATE_URL" >&2
  exit 1
fi

# Try to run seeder directly (for GitHub Actions service containers)
if command -v python &> /dev/null && [ -f "$SEED_SCRIPT" ]; then
  echo "Seeding Weaviate directly with Python..."
  export WEAVIATE_URL="$WEAVIATE_URL"
  python "$SEED_SCRIPT"
else
  # Fall back to docker compose (for local development)
  echo "Seeding Weaviate via the api container..."
  docker compose exec -T \
    -e WEAVIATE_URL="$WEAVIATE_URL" \
    api python /app/api/seed_weaviate.py
fi
echo "Done."
