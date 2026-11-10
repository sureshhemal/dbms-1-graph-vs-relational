#!/usr/bin/env bash
# Bring up Neo4j (Homebrew) and Memgraph (Docker on Colima), verify both are
# actually answering, then open their dashboards.
#
#   ./start-graph-dbs.sh            start everything and open the dashboards
#   ./start-graph-dbs.sh --no-open  start/verify only, don't open browser tabs
#
# Neo4j:    bolt://localhost:7687   dashboard http://localhost:7474/browser/
# Memgraph: bolt://localhost:7688   dashboard http://localhost:3000 (Memgraph Lab)

set -u

MEMGRAPH_COMPOSE="$HOME/memgraph/docker-compose.yml"
NEO4J_BROWSER_URL="http://localhost:7474/browser/"
MEMGRAPH_LAB_URL="http://localhost:3000"
OPEN_TABS=1
[ "${1:-}" = "--no-open" ] && OPEN_TABS=0

green() { printf '\033[32m✔ %s\033[0m\n' "$*"; }
yellow() { printf '\033[33m… %s\033[0m\n' "$*"; }
red() { printf '\033[31m✘ %s\033[0m\n' "$*"; }
die() { red "$*"; exit 1; }

# wait_for <seconds> <description> <command...>: retry command every 2s until it succeeds
wait_for() {
  local timeout=$1 desc=$2; shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS - start >= timeout )); then
      red "Timed out after ${timeout}s waiting for: $desc"
      return 1
    fi
    sleep 2
  done
}

http_code() { curl -s -o /dev/null -w '%{http_code}' -m 3 "$1"; }
neo4j_running() { neo4j status >/dev/null 2>&1; }
neo4j_bolt_up() { nc -z -w 2 127.0.0.1 7687; }
neo4j_browser_ok() { [ "$(http_code "$NEO4J_BROWSER_URL")" = 200 ]; }
neo4j_stopped() { ! neo4j_running; }

# Stop fully *before* starting again: launching while the old JVM is still
# shutting down makes the new one exit with "already running".
restart_neo4j() {
  yellow "Restarting Neo4j (clean stop, then start)..."
  brew services stop neo4j >/dev/null 2>&1
  neo4j_running && neo4j stop >/dev/null 2>&1   # in case it was started by hand
  wait_for 60 "Neo4j to stop" neo4j_stopped || die "Neo4j would not stop"
  brew services start neo4j >/dev/null 2>&1 || die "brew services start neo4j failed"
}

# ---------------------------------------------------------------- Neo4j
echo "== Neo4j"
if neo4j_running; then
  green "Neo4j already running"
else
  restart_neo4j
fi
wait_for 120 "Neo4j Bolt on 7687" neo4j_bolt_up || die "Neo4j did not come up (see /opt/homebrew/var/log/neo4j.log)"
wait_for 60 "Neo4j HTTP on 7474" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://localhost:7474/)\" != 000 ]" \
  || die "Neo4j HTTP is not answering"

# macOS purges the temp dir Neo4j unpacks the Browser into, which turns
# /browser/ into a 403 after a few days of uptime. A restart re-unpacks it.
if ! neo4j_browser_ok; then
  yellow "Neo4j Browser returned $(http_code "$NEO4J_BROWSER_URL") (stale temp files) - fixing"
  restart_neo4j
  wait_for 120 "Neo4j Bolt on 7687" neo4j_bolt_up || die "Neo4j did not come back up"
  wait_for 60 "Neo4j Browser" neo4j_browser_ok || die "Neo4j Browser still not loading"
fi
green "Neo4j up      bolt://localhost:7687   $NEO4J_BROWSER_URL"

# ---------------------------------------------------------------- Colima
echo "== Colima"
if colima status >/dev/null 2>&1; then
  green "Colima already running"
else
  yellow "Starting Colima (takes ~30-60s)..."
  colima start >/dev/null 2>&1 || die "colima start failed (try: colima start --verbose)"
fi
wait_for 60 "Docker daemon" docker info || die "Docker is not reachable through Colima"
green "Docker ready"

# ---------------------------------------------------------------- Memgraph
echo "== Memgraph"
[ -f "$MEMGRAPH_COMPOSE" ] || die "Compose file not found: $MEMGRAPH_COMPOSE"
# --pull never: don't silently upgrade to a new :latest image right before use
docker compose -f "$MEMGRAPH_COMPOSE" up -d --pull never >/dev/null 2>&1 \
  || die "docker compose up failed (run: docker compose -f $MEMGRAPH_COMPOSE up -d)"

memgraph_query_ok() {
  docker exec memgraph-mage bash -c 'echo "RETURN 1;" | mgconsole --host 127.0.0.1 --port 7687'
}
memgraph_lab_ok() { [ "$(http_code "$MEMGRAPH_LAB_URL")" = 200 ]; }

wait_for 120 "Memgraph to accept queries" memgraph_query_ok || die "Memgraph is not answering queries (docker logs memgraph-mage)"
wait_for 30 "Memgraph port 7688 on the host" nc -z -w 2 127.0.0.1 7688 || die "Memgraph port 7688 not forwarded to the host"
wait_for 60 "Memgraph Lab on 3000" memgraph_lab_ok || die "Memgraph Lab is not loading (docker logs memgraph-lab)"
green "Memgraph up   bolt://localhost:7688   $MEMGRAPH_LAB_URL"

# ---------------------------------------------------------------- Dashboards
echo
green "All good - both databases are up."
if (( OPEN_TABS )); then
  open "$NEO4J_BROWSER_URL"
  open "$MEMGRAPH_LAB_URL"
fi
