# Lab 2: Logs to RCA Using an LLM — Video Script

**Duration:** 12-15 minutes
**Style:** Screen recording with instructor voiceover

---

## Opening (1 minute)

> Welcome to Lab 2: Logs to RCA Using an LLM. In this lab, we are going to take raw incident logs from a real-world-style SmartDine outage and turn them into a structured Root Cause Analysis — the kind of report that normally takes an experienced SRE 30 to 60 minutes to write by hand.

> Here is the scenario: It is Friday at 7:15 PM, SmartDine's busiest hour. A deployment just went out that reduced the payment service's database connection pool from 100 connections down to 10. Under peak traffic, that pool exhausted in under 3 minutes. Payments started failing, which cascaded to orders, inventory, and notifications. 47 orders failed, about $1,400 in revenue was lost, and it took 8 minutes to find the root cause and roll back.

> We have 501 lines of logs across 3 different sources — nginx access logs, nginx error logs, and structured application logs. We are going to ingest all of them, filter for the signal, and generate an RCA automatically.

> Let's start the lab.

---

## Part 1 — Starting Up and Ingesting Logs (3 minutes)

> First, let's start the containers.

```bash
cd lab2-logs-rca-llm
docker compose up -d --build
```

> While that builds, let me explain what we are running. There are three containers: the RCA gateway on port 7000 — that is our main service — Prometheus on 9090 for metrics collection, and Grafana on 3000 for the dashboard.

> Let's check the health endpoint.

```bash
curl -s http://localhost:7000/health | python3 -m json.tool
```

> Good — status is healthy, mode is mock, and zero logs in memory. Mock mode means we get realistic LLM responses without needing an API key. The lab works fully without one.

> Now let's ingest the logs.

```bash
curl -s -X POST http://localhost:7000/ingest-logs | python3 -m json.tool
```

> 501 lines parsed from 3 files. Look at the severity breakdown — you can see the majority are INFO, with ERROR and WARN entries concentrated during the incident window. The gateway parsed three completely different log formats — CLF from nginx access, syslog-style from nginx error, and structured JSON from the application — into a single normalized format.

> Let's see the statistics.

```bash
curl -s http://localhost:7000/log-stats | python3 -m json.tool
```

> Notice that payment-service dominates the error count. That is our first clue.

---

## Part 2 — Filtering for Signal (2 minutes)

> Now here is the key insight. Of those 501 lines, most are normal operations — menu fetches, successful orders, health checks. The incident signal is buried in the errors and warnings. Let me show you the difference.

> First, let's filter for all logs.

```bash
curl -s -X POST http://localhost:7000/filter-logs \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool
```

> All 501 lines. Now let's filter for ERROR and WARN only.

```bash
curl -s -X POST http://localhost:7000/filter-logs \
  -H "Content-Type: application/json" \
  -d '{"severity": "ERROR,WARN"}' \
  | python3 -m json.tool
```

> Look at that reduction. We went from 501 lines down to just the incident-relevant entries. And look at the sample logs — every single one is about the failure. Connection pool timeouts, circuit breaker trips, payment failures, cascading errors to order-service. This is the signal.

> This filtering step is critical for two reasons. First, it focuses the LLM on what matters instead of drowning it in noise. Second, fewer input tokens means lower cost. You are paying per token with OpenAI — sending 500 lines of normal operations is a waste of money.

---

## Part 3 — Generating the Summary RCA (2 minutes)

> Let's generate our first RCA in summary format.

```bash
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool
```

> Four fields. Incident summary — identifies the connection pool misconfiguration in deployment D-4721. Root cause — db_pool_max changed from 100 to 10. Impact — 47 failed orders, $1,400 loss, 4 services affected. Resolution — rolled back to v2.8.0.

> That is a complete, structured RCA generated in milliseconds. An SRE would spend 30-60 minutes writing this up manually. Notice the token usage — prompt tokens for the log input, completion tokens for the RCA output. In live mode with a real API key, those are actual OpenAI charges.

> Let's also look at the cache.

```bash
curl -s http://localhost:7000/cache/stats | python3 -m json.tool
```

> One entry cached. If we call the same endpoint again with the same parameters, it returns instantly from cache — zero additional tokens consumed.

---

## Part 4 — Detailed RCA Format (2 minutes)

> The summary is great for a Slack message or a quick status update. But for a post-incident review or compliance report, you need the full picture. Let's switch to detailed format.

```bash
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{"severity_filter": "ERROR,WARN", "format": "detailed"}' \
  | python3 -m json.tool
```

> Seven sections instead of four. Look at the timeline — a minute-by-minute event sequence from the deployment at 19:14:50 all the way through to resolution at 19:23:25. Business impact is quantified: $1,400 loss, 47 failed orders, 4 services affected, time to detect 6 minutes, time to resolve 8 minutes.

> The root cause section breaks down not just what changed, but why it failed and what the contributing factors were — no pre-deployment validation, no canary gate checking pool metrics, rolling deployment completed too fast.

> Then you have 5 prioritized remediation steps from immediate to long-term, and 4 lessons learned for the blameless postmortem.

> Look at the token difference — detailed uses significantly more completion tokens than summary. This is the FinOps of AI. You choose the right output format based on the audience and the budget.

---

## Part 5 — Manual Edits (2 minutes)

> Now let's make two configuration changes to see how they affect the pipeline.

> First, let's change the default severity filter from ALL to ERROR,WARN.

```bash
nano +9 .env
```

> Line 9 — change `LOG_FILTER_SEVERITY=ALL` to `LOG_FILTER_SEVERITY=ERROR,WARN`. Save with Ctrl+O, Enter, Ctrl+X.

> Second, change the default RCA format from summary to detailed.

```bash
nano +12 .env
```

> Line 12 — change `RCA_OUTPUT_FORMAT=summary` to `RCA_OUTPUT_FORMAT=detailed`. Save and exit.

> Now restart the gateway.

```bash
docker compose restart rca-gateway
```

> Wait 10 seconds, then verify.

```bash
curl -s http://localhost:7000/config | python3 -m json.tool | grep -E '"log_filter_severity|rca_output_format"'
```

> The default filter is now ERROR,WARN and the default format is detailed. Let's re-ingest and test.

```bash
curl -s -X POST http://localhost:7000/ingest-logs > /dev/null
curl -s -X POST http://localhost:7000/generate-rca \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool | head -20
```

> Without specifying any parameters, the RCA now generates in detailed format with only ERROR and WARN logs. The configuration changes are reflected end to end.

---

## Part 6 — Grafana Dashboard (1 minute)

> Let's open Grafana at localhost:3000. Log in with admin/admin.

> The Lab 2 dashboard has 13 panels organized in 4 rows. The first row shows gateway performance — request rate, latency percentiles, and RCA generation time. Second row is the log pipeline — ingestion counts, filter operations, and RCA generation counts. Third row is token economics — cumulative token burn, error rate, and token burn rate per minute. The bottom row has four stat panels: logs in memory, total RCA reports, total tokens used, and cache entries.

> This is the meta-observability layer — monitoring your monitoring. In production, you would set alerts on these: if token burn rate exceeds your budget, if error rate spikes, or if ingestion drops to zero.

---

## Closing (1 minute)

> Let's clean up.

```bash
docker compose down
```

> Here is what we covered in this lab. We took 501 raw log lines from three different formats and parsed them into a unified schema. We filtered out 80% of the noise using severity-based filtering. We generated both summary and detailed RCA reports, compared the token economics of each format, and made two configuration changes to tune the pipeline defaults.

> The key takeaway: log-based RCA generation is the foundation of AIOps. You are taking what an SRE does in 30-60 minutes — reading logs, correlating timestamps, identifying the chain of events, writing up findings — and compressing it into a structured pipeline that runs in seconds. The LLM produces a first draft, the human reviews and refines. That is the right division of labor.

> In the next lab, we will build on this foundation by adding alert correlation and anomaly detection to the pipeline. See you there.
