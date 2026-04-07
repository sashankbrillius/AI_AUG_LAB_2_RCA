# Lab 2: Logs to RCA Using an LLM

A hands-on AIOps lab where you ingest multi-source incident logs and generate structured Root Cause Analysis reports using an LLM (mock mode by default — no API keys needed).

## Quick Start

```bash
cd lab2-logs-rca-llm
docker compose up -d --build
curl -s http://localhost:7000/health | python3 -m json.tool
```

## What You Will Learn

- **Multi-source log parsing** — Ingest nginx access logs (CLF), nginx error logs (syslog), and structured JSON application logs into a unified format
- **Severity-based filtering** — Isolate incident signal from operational noise (501 lines → ERROR/WARN only)
- **Structured RCA generation** — Produce summary (4-field) and detailed (7-section) Root Cause Analysis reports
- **Token economics** — Track prompt vs completion token usage as a cost proxy for LLM-based analysis
- **Configuration tuning** — Change severity filters and output formats via `.env` edits to see real-time pipeline impact

## Incident Scenario

SmartDine deployment D-4721 (payment-service v2.8.1, change PAY-88) reduced `db_pool_max` from 100 to 10 during peak Friday evening traffic. The pool exhausted in 3 minutes, triggering cascading failures across 4 services: 47 failed orders, ~$1,400 revenue loss, 8-minute resolution via rollback.

## Architecture

| Service | Port | Purpose |
|---------|------|---------|
| rca-gateway | 7000 | Log ingestion, filtering, RCA generation |
| prometheus | 9090 | Metrics collection |
| grafana | 3000 | 13-panel dashboard (admin/admin) |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /ingest-logs | Parse and load log files into memory |
| POST | /filter-logs | Filter logs by severity level |
| POST | /generate-rca | Generate structured RCA report |
| GET | /health | Service health check |
| GET | /config | Current configuration |
| GET | /log-stats | Log statistics and breakdowns |
| POST | /cache/clear | Clear RCA cache |
| GET | /cache/stats | View cached RCA entries |
| GET | /metrics | Prometheus metrics |

## Log Datasets

| File | Lines | Format | Content |
|------|-------|--------|---------|
| nginx_access.log | 232 | CLF | HTTP requests with status codes and response times |
| nginx_error.log | 71 | Syslog | Upstream connection errors, timeouts, SSL failures |
| smartdine_app.log | 198 | JSON | Structured logs from payment, order, inventory, notification services |

## Manual Edits

Two `.env` edits demonstrate configuration-driven pipeline behavior:

1. **Severity filter:** `LOG_FILTER_SEVERITY=ALL` → `ERROR,WARN` (line 9)
2. **Output format:** `RCA_OUTPUT_FORMAT=summary` → `detailed` (line 12)

After editing, run `docker compose restart rca-gateway`.

## Files

```
lab2-logs-rca-llm/
├── docker-compose.yml
├── .env / .env.example
├── smoke-test.sh
├── README.md
├── video-script.md
├── docs/
│   └── instructions.md
├── datasets/incident_logs/
│   ├── nginx_access.log
│   ├── nginx_error.log
│   └── smartdine_app.log
├── services/rca-gateway/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/main.py
├── shared/
│   └── telemetry.py
└── ops/
    ├── configs/
    │   ├── prometheus.yml
    │   ├── alerts.yml
    │   └── grafana/provisioning/
    └── dashboards/
        └── lab2-logs-rca.json
```

## Duration

30-45 minutes

## Cleanup

```bash
docker compose down
```
# AI_AUG_LAB_2_RCA
