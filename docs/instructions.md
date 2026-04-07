# Lab 2: Logs to RCA Using an LLM — Mega-Lab

**Duration:** 30-45 minutes
**Mode:** Mock (no API keys needed — works fully with simulated LLM responses)
**Topics Covered:** Log ingestion, multi-source log parsing, severity filtering, structured Root Cause Analysis (RCA) generation, LLM token economics

---

## Scenario: SmartDine Peak-Hour Payment Meltdown

It is Friday at 7:15 PM — SmartDine's busiest hour. Deployment D-4721 just rolled out `payment-service v2.8.1` with a seemingly innocent configuration change (ticket PAY-88). Within minutes, orders start failing, customers are stuck at checkout, and the on-call team is buried in alerts.

Here is what went wrong:

1. **Misconfigured connection pool** — The change reduced `db_pool_max` from 100 to 10. Under peak traffic (~50 concurrent payment requests), the pool exhausted in under 3 minutes.

2. **Cascading failure across 4 services** — Payment failures cascaded to order-service (couldn't confirm payments), inventory-service (stuck reservations), and notification-service (backed-up email queue).

3. **47 failed orders, ~$1,400 lost revenue** — Customers saw 502 errors for 8 minutes until the on-call engineer identified the root cause and rolled back.

4. **300+ log lines across 3 sources** — Nginx access logs, nginx error logs, and structured application logs all contain pieces of the story. No single log source tells the whole picture.

Your job: Ingest these raw logs, filter for the signal in the noise, and use an LLM to generate a structured Root Cause Analysis that would normally take an experienced SRE 30-60 minutes to write manually.

---

## Getting Started

### 1 — Start the Lab

```bash
cd lab2-logs-rca-llm
docker compose up -d --build
```

Wait about 30 seconds, then verify everything is running:

```bash
curl -s http://localhost:7000/health | python3 -m json.tool
```

You should see `"ok": true`, `"mode": "mock"`, and `"log_filter_severity": "ALL"`.

### 2 — Check Current Configuration

```bash
curl -s http://localhost:7000/config | python3 -m json.tool
```

**What to observe:**
- `mode` — "mock" (LLM responses are simulated — no API key needed)
- `log_filter_severity` — "ALL" (all severity levels included)
- `rca_output_format` — "summary" (brief 4-field RCA)
- `logs_in_memory` — 0 (no logs loaded yet)
- `model` — "gpt-4o-mini" (the model that would be used in live mode)

### 3 — Open Grafana

Open http://localhost:3000 in your browser (login: `admin` / `admin`, skip password change). You should see the **Lab 2 — SmartDine Logs to RCA Dashboard** with 13 panels. Most panels will show "No data" until you start running commands.

### Troubleshooting — Getting Started

| Problem | Cause | Fix |
|---------|-------|-----|
| `docker compose up` fails | Docker daemon not running | Run `sudo systemctl start docker` and retry |
| Port 7000/9090/3000 already in use | Another lab using the port | Run `docker ps` to find the conflict, then `docker stop <container_id>` |
| Health check returns "Connection refused" | Service still starting | Wait 30-60 seconds and retry; run `docker compose logs rca-gateway` for errors |
| Grafana shows "No data" | Expected before running commands | Panels populate as you work through the lab |

---

## Part 1 — Log Ingestion (Multi-Source Parsing)

**Goal:** Load raw logs from three different sources and parse them into a unified in-memory store.

### Why This Matters

In a real incident, logs come from everywhere — web servers, application services, infrastructure monitors — each with its own format. An SRE has to mentally stitch together nginx access logs (CLF format), nginx error logs (syslog-like), and structured JSON application logs. An AIOps pipeline needs to parse all of them into a common schema before any analysis can happen.

SmartDine's incident produced 501 log lines across 3 files:
- **nginx_access.log** (232 lines) — HTTP requests with status codes and response times
- **nginx_error.log** (71 lines) — Upstream connection errors and timeouts
- **smartdine_app.log** (198 lines) — Structured JSON from payment-service, order-service, inventory-service, and others

### 1.1 — Ingest All Log Files

```bash
curl -s -X POST http://localhost:7000/ingest-logs | python3 -m json.tool
```

**What to observe:**
- `files_processed` — 3 (one per log source)
- `total_lines` — 501 (the full incident dataset)
- `lines_parsed` — All or nearly all lines successfully parsed
- `lines_skipped` — 0 (every line matches a known format)
- `severity_breakdown` — Shows how many INFO, WARN, ERROR lines exist across all sources
- Notice the mix of severities — the majority are INFO (normal operations), with ERROR and WARN concentrated during the incident window

### 1.2 — View Log Statistics

```bash
curl -s http://localhost:7000/log-stats | python3 -m json.tool
```

**What to observe:**
- `source_breakdown` — How many lines came from each log source (nginx_access, nginx_error, smartdine_app)
- `severity_breakdown` — The ratio of INFO to WARN to ERROR tells you how much of the log data is "noise" (normal operations) vs "signal" (incident-related)
- `service_breakdown` — Which services appear in the structured app logs (payment-service dominates the errors)

### Troubleshooting — Part 1

| Problem | Cause | Fix |
|---------|-------|-----|
| `files_processed: 0` | Datasets not copied into image | Run `docker compose up -d --build` (the `--build` flag is required to copy datasets) |
| `lines_skipped` is high | Unexpected log format | Run `docker exec rca-gateway ls /app/datasets/incident_logs/` to verify files exist |
| Different line counts | Log files modified | Re-extract the lab zip to restore original datasets |

---

## Part 2 — Log Filtering (Signal vs Noise)

**Goal:** Filter the ingested logs by severity level to focus on the incident-relevant entries.

### Why This Matters

The biggest challenge in incident response is not having too little data — it is having too much. Of 501 log lines, most are normal INFO-level operations (menu fetches, successful orders, health checks). The actual incident signal is buried in the ERROR and WARN entries. Feeding all 501 lines to an LLM wastes tokens and can confuse the model with irrelevant context. Smart filtering focuses the LLM on what matters.

### 2.1 — Filter for All Logs (Default)

```bash
curl -s -X POST http://localhost:7000/filter-logs \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool
```

**What to observe:**
- `filter_applied` — "ALL" (current default from `.env`)
- `total_logs` — The full ingested count
- `filtered_count` — Same as total (no filtering applied)
- `severity_breakdown` — The full breakdown including INFO, WARN, ERROR
- `sample_logs` — First 10 entries showing a mix of normal and error logs

### 2.2 — Filter for Errors Only

```bash
curl -s -X POST http://localhost:7000/filter-logs \
  -H "Content-Type: application/json" \
  -d '{"severity": "ERROR"}' \
  | python3 -m json.tool
```

**What to observe:**
- `filtered_count` — Dramatically fewer lines than the total
- `severity_breakdown` — Only ERROR entries remain
- `sample_logs` — Every entry is an error: connection pool timeouts, circuit breaker trips, payment failures, upstream connection refused
- Notice how much clearer the incident becomes when the noise is removed

### 2.3 — Filter for Errors and Warnings

```bash
curl -s -X POST http://localhost:7000/filter-logs \
  -H "Content-Type: application/json" \
  -d '{"severity": "ERROR,WARN"}' \
  | python3 -m json.tool
```

**What to observe:**
- `filtered_count` — More lines than ERROR-only, but still much less than ALL
- `severity_breakdown` — Both ERROR and WARN entries included
- The WARN entries are valuable — they show the "leading indicators" like pool nearing capacity and slow queries that appeared BEFORE the first ERROR
- This is the sweet spot for RCA: enough context to see the progression, but without the noise of 300+ INFO lines

### Troubleshooting — Part 2

| Problem | Cause | Fix |
|---------|-------|-----|
| `"No logs ingested yet"` error | Forgot to run ingest first | Run `curl -s -X POST http://localhost:7000/ingest-logs` first |
| `filtered_count: 0` | Severity value not matching | Use uppercase: `ERROR`, `WARN`, `INFO` — case-sensitive |
| Sample logs look normal | Severity filter too broad | Try `ERROR` only to isolate incident entries |

---

## Part 3 — RCA Generation (Summary Format)

**Goal:** Generate a structured Root Cause Analysis from the filtered logs using the default summary format.

### Why This Matters

Writing a Root Cause Analysis after an incident is one of the most time-consuming parts of incident response. A senior SRE typically spends 30-60 minutes reviewing logs, correlating timestamps, identifying the chain of events, and writing up the findings. An LLM can produce a structured first draft in seconds — not replacing human judgment, but accelerating the process from an hour to minutes.

The summary format gives you the 4 essential fields: what happened, why it happened, what the impact was, and how it was resolved.

### 3.1 — Generate Summary RCA

```bash
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool
```

**What to observe:**
- `format` — "summary" (4 fields)
- `mode` — "mock" (simulated LLM response)
- `model` — Shows which model would be used in live mode
- `logs_analyzed` — How many log lines went into the analysis
- `severity_filter` — Which severities were included (currently ALL)
- `rca.incident_summary` — A concise paragraph identifying the payment-service connection pool misconfiguration
- `rca.root_cause` — Deployment D-4721 changed `db_pool_max` from 100 to 10
- `rca.impact` — 47 failed orders, ~$1,400 revenue loss, 4 services affected
- `rca.resolution` — Rolled back to v2.8.0 via D-4722
- `tokens_used` — Prompt vs completion token counts (cost tracking)
- `latency_ms` — How long the RCA generation took

### 3.2 — Generate RCA with Error-Only Filter

```bash
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{"severity_filter": "ERROR,WARN"}' \
  | python3 -m json.tool
```

**What to observe:**
- `logs_analyzed` — Significantly fewer lines than the ALL filter
- `tokens_used` — Lower token count (fewer logs to process = lower cost)
- The RCA content should be the same quality but generated with fewer input tokens
- This demonstrates the economic benefit of filtering: same quality RCA at lower LLM cost

### 3.3 — Check RCA Cache

```bash
curl -s http://localhost:7000/cache/stats | python3 -m json.tool
```

**What to observe:**
- `cache_size` — Should show 2 (one for each unique combination of severity filter + format)
- `cache_keys` — The specific combinations cached
- Repeated calls with the same parameters return cached results instantly, avoiding duplicate LLM calls

### Troubleshooting — Part 3

| Problem | Cause | Fix |
|---------|-------|-----|
| `"No logs ingested yet"` error | Need to ingest first | Run `curl -s -X POST http://localhost:7000/ingest-logs` |
| RCA content is generic | Running in mock mode (expected) | Mock mode returns realistic but pre-built RCA; live mode would analyze actual logs |
| Token counts seem low | Mock mode estimates tokens | In live mode with a real API key, actual OpenAI token counts appear |
| Same result on re-run | Cache hit | Run `curl -s -X POST http://localhost:7000/cache/clear` to force regeneration |

---

## Part 4 — RCA Generation (Detailed Format)

**Goal:** Switch to the detailed RCA format to see the full incident timeline, business impact analysis, remediation steps, and lessons learned.

### Why This Matters

The summary format is great for a quick Slack message or status update. But for a post-incident review, compliance report, or blameless postmortem, you need the full picture: a minute-by-minute timeline, quantified business impact, a structured root cause breakdown with contributing factors, numbered remediation steps, and lessons learned. This is where the LLM's ability to synthesize 500+ log lines into a structured narrative saves the most time.

### 4.1 — Generate Detailed RCA

```bash
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{"severity_filter": "ERROR,WARN", "format": "detailed"}' \
  | python3 -m json.tool
```

**What to observe:**
- `format` — "detailed" (7 sections instead of 4)
- `rca.timeline` — Minute-by-minute event sequence from deployment to resolution:
  - 19:14:50 — D-4721 deployment starts
  - 19:15:30 — First WARN: pool nearing capacity
  - 19:16:00 — First ERROR: connection pool timeout
  - 19:16:30 — Circuit breaker opens
  - 19:17:00 — Cascade to order-service
  - 19:17:30 — Critical alert fires (92% error rate)
  - 19:17:55 — PagerDuty page sent (INC-0198, P1)
  - 19:21:25 — Root cause identified
  - 19:21:35 — Rollback initiated
  - 19:23:25 — Incident resolved
- `rca.business_impact` — Quantified: $1,400 loss, 47 failed orders, 4 services, TTD 6 min, TTR 8 min
- `rca.root_cause` — Structured breakdown with category, deployment ID, change ID, what/why/contributing factors
- `rca.remediation_steps` — 5 prioritized actions from immediate rollback to long-term config governance
- `rca.lessons_learned` — 4 systemic insights about configuration review rigor, automated policy checks, and cascade detection
- `tokens_used` — Higher completion tokens than summary format (more output = more tokens = higher cost)

### 4.2 — Compare Token Usage: Summary vs Detailed

```bash
echo "--- Summary format ---"
curl -s -X POST http://localhost:7000/cache/clear > /dev/null
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{"severity_filter": "ERROR,WARN", "format": "summary"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Tokens: {d[\"tokens_used\"][\"total\"]}  Latency: {d[\"latency_ms\"]}ms')"

echo "--- Detailed format ---"
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{"severity_filter": "ERROR,WARN", "format": "detailed"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Tokens: {d[\"tokens_used\"][\"total\"]}  Latency: {d[\"latency_ms\"]}ms')"
```

**What to observe:**
- Detailed format uses significantly more completion tokens than summary
- In a real production environment with OpenAI pricing, this difference translates directly to cost
- The tradeoff: summary is cheaper and faster; detailed is more comprehensive but costs more
- This is the "FinOps of AI" — choosing the right output granularity based on the use case

### Troubleshooting — Part 4

| Problem | Cause | Fix |
|---------|-------|-----|
| Detailed format looks the same as summary | Cache returning stale result | Run `curl -s -X POST http://localhost:7000/cache/clear` first |
| Token comparison shows same numbers | Both hitting cache | Clear cache before each generation: `curl -s -X POST http://localhost:7000/cache/clear` |
| Timeline is missing events | Running in mock mode (expected) | Mock mode returns a complete pre-built timeline; live mode extracts events from actual logs |

---

## Part 5 — Prometheus Metrics & Grafana Dashboard

**Goal:** Explore the observability metrics that the RCA gateway exposes and see them visualized in Grafana.

### Why This Matters

Every production AIOps pipeline needs to be observed itself. How many logs are being ingested? How often are RCAs being generated? How many tokens are being consumed? What is the error rate of the gateway? These meta-metrics let you monitor your monitoring — catching issues like token budget overruns, ingestion failures, or gateway overload before they impact incident response.

### 5.1 — Query Prometheus Directly

```bash
curl -s "http://localhost:9090/api/v1/query?query=rca_generated_total" \
  | python3 -m json.tool
```

**What to observe:**
- The counter shows how many RCA reports have been generated, broken down by format (summary/detailed) and mode (mock/llm)
- Each call to `/generate-rca` increments this counter

```bash
curl -s "http://localhost:9090/api/v1/query?query=llm_tokens_total" \
  | python3 -m json.tool
```

**What to observe:**
- Token usage tracked by direction (prompt vs completion) and model
- In mock mode, tokens are estimated; in live mode, actual OpenAI usage is recorded
- This is the cost signal — if this counter is climbing fast, your LLM spend is too

```bash
curl -s "http://localhost:9090/api/v1/query?query=logs_ingested_total" \
  | python3 -m json.tool
```

**What to observe:**
- Ingestion counts by source (nginx_access, nginx_error, smartdine_app)
- Useful for detecting if a log source stops sending data or suddenly spikes

### 5.2 — View the Grafana Dashboard

Open http://localhost:3000 and navigate to the **Lab 2 — SmartDine Logs to RCA Dashboard**.

**What to observe across the 13 panels:**

**Row 1 — Gateway Performance:**
- **Request Rate by Endpoint** — Requests/sec to each endpoint (/ingest-logs, /filter-logs, /generate-rca)
- **Gateway Latency (p50/p95)** — Response time distribution for all API calls
- **RCA Generation Latency** — Specifically how long the RCA endpoint takes (mock: ~100-400ms, live: 2-10s)

**Row 2 — Log Pipeline:**
- **Logs Ingested by Source** — Cumulative ingestion from each log file
- **Filter Operations by Severity** — Which severity filters are being used most
- **RCA Reports Generated** — Count by format (summary vs detailed) and mode (mock vs llm)

**Row 3 — Token Economics:**
- **Token Burn (Cost Proxy)** — Cumulative token usage over the last hour
- **Error Rate (5xx %)** — Gateway error rate (should be 0% under normal operation)
- **Token Burn Rate (per minute)** — Token velocity — how fast you are consuming LLM capacity

**Row 4 — Status Gauges:**
- **Logs in Memory** — Current count of parsed log lines
- **Total RCA Reports** — Lifetime RCA generation count
- **Total Tokens Used** — Lifetime token consumption
- **RCA Cache Entries** — How many unique RCA results are cached

### Troubleshooting — Part 5

| Problem | Cause | Fix |
|---------|-------|-----|
| Prometheus query returns empty | Metrics not yet generated | Run through Parts 1-4 first to generate metrics |
| Grafana panels show "No data" | Prometheus datasource not connected | Check Grafana > Settings > Data Sources > Prometheus URL is `http://prometheus:9090` |
| Token panels are flat | Cache hits don't generate new tokens | Clear cache and re-run RCA to see token counters increment |

---

## Linux File Editing Quick Reference

Since this lab runs inside a Linux VM, you will use terminal-based editors to modify files. Here are the two most common options:

**Option A — nano (recommended for beginners):**
```bash
nano <filepath>
```
- Navigate with arrow keys to the line you need to edit
- Make your changes directly
- Press `Ctrl+O` then `Enter` to save
- Press `Ctrl+X` to exit

**Option B — vi/vim (for experienced users):**
```bash
vi <filepath>
```
- Press `i` to enter Insert mode
- Navigate to the target line and make your edit
- Press `Esc` to exit Insert mode
- Type `:wq` and press `Enter` to save and exit
- To exit WITHOUT saving: press `Esc`, type `:q!`, press `Enter`

**Helpful commands:**
```bash
# Find the exact line number before editing:
grep -n "search_text" <filepath>

# Show a file with line numbers:
cat -n <filepath> | head -20

# Jump directly to a line in nano:
nano +9 <filepath>

# Jump directly to a line in vi:
vi +9 <filepath>
```

---

## Part 6 — Manual Edits

Now make two configuration changes to see how filtering and output format affect the RCA pipeline.

### Edit 1: Change the Severity Filter (Log Focus)

The current filter is `ALL`, which sends all 501 log lines (including hundreds of normal INFO entries) to the RCA engine. Change it to `ERROR,WARN` to focus on incident-relevant entries only.

> **Editing tip:** Open the file with `nano +9 .env` or `vi +9 .env` to jump directly to the line.

**File:** `.env`
**Find (line 9):**
```
LOG_FILTER_SEVERITY=ALL
```

**Change to:**
```
LOG_FILTER_SEVERITY=ERROR,WARN
```

### Edit 2: Change the RCA Output Format (Detail Level)

The current output format is `summary` (4 fields). Change to `detailed` to get the full incident timeline, business impact, remediation steps, and lessons learned by default.

> **Editing tip:** Open the file with `nano +12 .env` or `vi +12 .env` to jump directly to the line.

**File:** `.env`
**Find (line 12):**
```
RCA_OUTPUT_FORMAT=summary
```

**Change to:**
```
RCA_OUTPUT_FORMAT=detailed
```

### Restart and Verify

```bash
docker compose restart rca-gateway
```

Wait 10 seconds, then verify:

```bash
curl -s http://localhost:7000/config | python3 -m json.tool | grep -E '"log_filter_severity|rca_output_format"'
```

You should see `"log_filter_severity": "ERROR,WARN"` and `"rca_output_format": "detailed"`.

### Test the Impact

**Re-ingest logs (restart clears memory):**

```bash
curl -s -X POST http://localhost:7000/ingest-logs | python3 -m json.tool
```

**Default filter now uses ERROR,WARN:**

```bash
curl -s -X POST http://localhost:7000/filter-logs \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool | grep -E '"filter_applied|total_logs|filtered_count"'
```

You should see `filter_applied: "ERROR,WARN"` and `filtered_count` much lower than `total_logs`. The noise has been removed by default.

**Default RCA now generates detailed format:**

```bash
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool
```

The RCA should now include `timeline`, `business_impact`, `root_cause` (with contributing factors), `remediation_steps`, and `lessons_learned` — all by default without specifying the format parameter.

### Troubleshooting — Manual Edits

| Problem | Cause | Fix |
|---------|-------|-----|
| Config still shows old values | Forgot to restart | Run `docker compose restart rca-gateway` (not just `docker compose up`) |
| Container crashes after edit | Syntax error in `.env` | Check for typos — no spaces around `=`, no quotes needed |
| Filter still showing ALL | Wrong line edited | Use `grep -n "LOG_FILTER_SEVERITY" .env` to find the correct line |
| RCA still returns summary format | Cache returning old result | Cache is cleared on restart; if still wrong, check `rca_output_format` in config |
| "No logs ingested" after restart | Restart clears in-memory store | Run `curl -s -X POST http://localhost:7000/ingest-logs` after every restart |

**Check Grafana** — After running the post-edit commands, the dashboard should show updated filter operations and RCA generation metrics.

---

## Cleanup

```bash
docker compose down
```

---

## Key Takeaways

| Concept | Before (Manual) | After (AIOps Pipeline) | Improvement |
|---------|-----------------|----------------------|-------------|
| Log parsing | SRE reads 3 different formats manually | Multi-source parser normalizes all formats in seconds | Minutes → milliseconds |
| Signal isolation | Scrolling through 501 lines for errors | Severity filter reduces to ERROR/WARN only | 80%+ noise reduction |
| RCA writing | SRE spends 30-60 min writing postmortem | LLM generates structured RCA in <1 second (mock) or 2-10 sec (live) | 60x faster first draft |
| Output granularity | One-size-fits-all incident report | Summary (Slack update) vs Detailed (postmortem) on demand | Right format for each audience |
| Cost visibility | No idea how much AI analysis costs | Token tracking per request, per direction (prompt/completion) | Full FinOps transparency |
| Caching | Every query hits the LLM | Identical queries served from cache | Zero marginal cost on repeats |
