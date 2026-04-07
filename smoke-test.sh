#!/usr/bin/env bash
set -euo pipefail

PASS=0
FAIL=0
WARN=0

pass() { echo "  PASS: $1"; ((PASS++)); }
fail() { echo "  FAIL: $1"; ((FAIL++)); }
warn() { echo "  WARN: $1"; ((WARN++)); }

check_json_field() {
  local url="$1" label="$2" field="$3" expected="$4" method="${5:-GET}"
  local resp
  if [ "$method" = "POST" ]; then
    resp=$(curl -sf -X POST "$url" 2>/dev/null) || { fail "$label — connection refused"; return; }
  else
    resp=$(curl -sf "$url" 2>/dev/null) || { fail "$label — connection refused"; return; }
  fi
  local val
  val=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$field',''))" 2>/dev/null) || { fail "$label — JSON parse error"; return; }
  if [ "$val" = "$expected" ]; then
    pass "$label"
  else
    fail "$label — expected $field='$expected', got '$val'"
  fi
}

echo "============================================"
echo " Lab 2 — Logs to RCA Smoke Test"
echo "============================================"
echo ""

echo "[1/5] Health checks"
check_json_field "http://localhost:7000/health" "rca-gateway health" "ok" "True"
PROM=$(curl -sf "http://localhost:9090/-/healthy" 2>/dev/null) && pass "prometheus health" || fail "prometheus health — connection refused"
echo ""

echo "[2/5] Metrics endpoint"
METRICS=$(curl -sf http://localhost:7000/metrics 2>/dev/null) || { fail "rca-gateway metrics — connection refused"; }
if echo "$METRICS" | python3 -c "import sys; data=sys.stdin.read(); exit(0 if 'http_requests_total' in data else 1)" 2>/dev/null; then
  pass "rca-gateway metrics"
else
  fail "rca-gateway metrics — missing http_requests_total"
fi
echo ""

echo "[3/5] Log ingestion"
INGEST=$(curl -sf -X POST http://localhost:7000/ingest-logs 2>/dev/null) || { fail "Log ingest — connection refused"; }
PARSED=$(echo "$INGEST" | python3 -c "import sys,json; print(json.load(sys.stdin).get('lines_parsed',0))" 2>/dev/null || echo "0")
if [ "$PARSED" -gt 0 ]; then
  pass "Log ingest ($PARSED lines parsed)"
else
  fail "Log ingest returned 0 parsed lines"
fi
echo ""

echo "[4/5] Log filtering"
FILTER=$(curl -sf -X POST http://localhost:7000/filter-logs \
  -H "Content-Type: application/json" \
  -d '{"severity": "ERROR"}' 2>/dev/null) || { fail "Log filter — connection refused"; }
COUNT=$(echo "$FILTER" | python3 -c "import sys,json; print(json.load(sys.stdin).get('filtered_count',0))" 2>/dev/null || echo "0")
if [ "$COUNT" -gt 0 ]; then
  pass "Log filter returned $COUNT ERROR lines"
else
  warn "Log filter returned 0 ERROR lines"
fi
echo ""

echo "[5/5] RCA generation"
RCA=$(curl -sf -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{"severity_filter": "ERROR,WARN", "format": "summary"}' 2>/dev/null) || { fail "RCA generation — connection refused"; }
HAS_SUMMARY=$(echo "$RCA" | python3 -c "import sys,json; rca=json.load(sys.stdin).get('rca',{}); print('yes' if 'incident_summary' in rca else 'no')" 2>/dev/null || echo "no")
if [ "$HAS_SUMMARY" = "yes" ]; then
  pass "RCA generation returns structured output"
else
  fail "RCA generation did not return expected structure"
fi
HAS_TOKENS=$(echo "$RCA" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'tokens_used' in d else 'no')" 2>/dev/null || echo "no")
if [ "$HAS_TOKENS" = "yes" ]; then
  pass "RCA generation includes token usage"
else
  warn "RCA generation missing token usage"
fi
echo ""

echo "============================================"
echo " Results: $PASS passed, $FAIL failed, $WARN warnings"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "Some checks failed. Run 'docker compose ps' to check container status."
  echo "Run 'docker logs rca-gateway' to see error details."
  exit 1
fi
