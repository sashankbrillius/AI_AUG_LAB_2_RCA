import json
import logging
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from shared.telemetry import (
    LOG_FILTER,
    LOG_INGEST,
    LLM_TOKENS,
    RCA_GENERATED,
    RCA_LATENCY,
    prom_middleware,
)

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rca-gateway")

SERVICE = "rca-gateway"

DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
RCA_MODE = os.environ.get("RCA_MODE", "mock").lower()
LOG_FILTER_SEVERITY = os.environ.get("LOG_FILTER_SEVERITY", "ALL")
RCA_OUTPUT_FORMAT = os.environ.get("RCA_OUTPUT_FORMAT", "summary")

LOGS_INGESTED = Gauge("logs_store_total", "Total log lines currently in memory", ["service"])
LOGS_BY_SEVERITY = Gauge("logs_by_severity", "Log lines by severity level", ["service", "severity"])
RCA_CACHE_SIZE = Gauge("rca_cache_size", "Current RCA cache size", ["service"])
FILTER_SEVERITY_GAUGE = Gauge("log_filter_severity_info", "Current severity filter (1=ALL 2=ERROR 3=WARN 4=ERROR,WARN)", ["service", "setting"])
RCA_FORMAT_GAUGE = Gauge("rca_output_format_info", "Current RCA format (1=summary 2=detailed)", ["service", "format"])

app = FastAPI(title="SmartDine Log Analysis & RCA Gateway", version="1.0")
app.middleware("http")(prom_middleware(SERVICE))

_LOG_STORE: List[Dict] = []
_RCA_CACHE: Dict[str, dict] = {}

DATASETS_DIR = os.environ.get("DATASETS_DIR", "/app/datasets/incident_logs")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": str(exc), "type": type(exc).__name__, "path": request.url.path})


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(status_code=404, content={"error": "not found", "path": request.url.path})


@app.exception_handler(405)
async def method_not_allowed_handler(request: Request, exc):
    return JSONResponse(status_code=405, content={"error": "method not allowed", "method": request.method, "path": request.url.path})


def _parse_nginx_access_line(line: str) -> Optional[Dict]:
    pattern = r'^(\S+) - - \[([^\]]+)\] "(\S+) (\S+) (\S+)" (\d+) (\d+) "([^"]*)" "([^"]*)" ([\d.]+)$'
    m = re.match(pattern, line.strip())
    if not m:
        return None
    status = int(m.group(6))
    resp_time = float(m.group(10))
    if status >= 500:
        severity = "ERROR"
    elif status >= 400 or resp_time > 2.0:
        severity = "WARN"
    else:
        severity = "INFO"
    return {
        "source": "nginx_access",
        "timestamp": m.group(2),
        "severity": severity,
        "client_ip": m.group(1),
        "method": m.group(3),
        "path": m.group(4),
        "status": status,
        "bytes": int(m.group(7)),
        "user_agent": m.group(9),
        "response_time": resp_time,
        "raw": line.strip(),
    }


def _parse_nginx_error_line(line: str) -> Optional[Dict]:
    pattern = r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.+)$'
    m = re.match(pattern, line.strip())
    if not m:
        return None
    level_map = {"emerg": "ERROR", "alert": "ERROR", "crit": "ERROR", "error": "ERROR", "warn": "WARN", "notice": "INFO", "info": "INFO", "debug": "DEBUG"}
    severity = level_map.get(m.group(2).lower(), "INFO")
    return {
        "source": "nginx_error",
        "timestamp": m.group(1),
        "severity": severity,
        "nginx_level": m.group(2),
        "message": m.group(3),
        "raw": line.strip(),
    }


def _parse_app_log_line(line: str) -> Optional[Dict]:
    try:
        data = json.loads(line.strip())
        return {
            "source": "smartdine_app",
            "timestamp": data.get("ts", ""),
            "severity": data.get("level", "INFO"),
            "service": data.get("service", "unknown"),
            "version": data.get("version", ""),
            "deployment": data.get("deployment", ""),
            "message": data.get("msg", ""),
            "fields": {k: v for k, v in data.items() if k not in ("ts", "level", "service", "version", "deployment", "msg")},
            "raw": line.strip(),
        }
    except json.JSONDecodeError:
        return None


class IngestResponse(BaseModel):
    ok: bool
    files_processed: int
    total_lines: int
    lines_parsed: int
    lines_skipped: int
    severity_breakdown: dict


class FilterRequest(BaseModel):
    severity: Optional[str] = None


class FilterResponse(BaseModel):
    filter_applied: str
    total_logs: int
    filtered_count: int
    severity_breakdown: dict
    sample_logs: list


class RCARequest(BaseModel):
    severity_filter: Optional[str] = None
    format: Optional[str] = None


class RCAResponse(BaseModel):
    format: str
    mode: str
    model: str
    logs_analyzed: int
    severity_filter: str
    rca: dict
    tokens_used: dict
    latency_ms: float


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 4))


MOCK_RCA_SUMMARY = {
    "incident_summary": "Payment service cascading failure caused by database connection pool misconfiguration in deployment D-4721 (v2.8.1, change PAY-88). Pool reduced from 100 to 10 connections, exhausting under peak traffic.",
    "root_cause": "Deployment D-4721 changed db_pool_max from 100 to 10. Under peak Friday evening traffic, 10 connections saturated within 3 minutes, triggering circuit breaker and cascading to order-service, inventory-service, and notification-service.",
    "impact": "47 failed orders, ~$1,400 estimated revenue loss, 4 services affected, 8-minute time-to-resolution.",
    "resolution": "Rolled back to v2.8.0 (D-4722), restoring db_pool_max=100. Services recovered within 2 minutes of rollback.",
}

MOCK_RCA_DETAILED = {
    "incident_summary": "Payment service cascading failure caused by database connection pool misconfiguration in deployment D-4721 (v2.8.1, change PAY-88). Pool reduced from 100 to 10 connections, exhausting under peak traffic.",
    "timeline": [
        {"time": "19:14:50", "event": "Deployment D-4721 started — payment-service v2.8.1 rolling out (change PAY-88)"},
        {"time": "19:15:05", "event": "Deployment complete — payment-service v2.8.1 live with db_pool_max=10 (was 100)"},
        {"time": "19:15:30", "event": "First WARN: database connection pool nearing capacity (8/10 active)"},
        {"time": "19:15:50", "event": "Pool exhausted (10/10 active, 5 requests queuing)"},
        {"time": "19:16:00", "event": "First ERROR: database connection pool timeout (5000ms)"},
        {"time": "19:16:15", "event": "Payment failures begin — ConnectionPoolExhausted errors"},
        {"time": "19:16:30", "event": "Circuit breaker OPEN — all database connections unavailable"},
        {"time": "19:17:00", "event": "Cascade begins — order-service reports payment dependency unavailable"},
        {"time": "19:17:30", "event": "Alert fired: PaymentServiceErrorRateHigh (92% error rate)"},
        {"time": "19:17:45", "event": "Alert fired: OrderServiceErrorRateElevated (35% error rate)"},
        {"time": "19:17:55", "event": "PagerDuty page sent — INC-0198 opened, P1 priority"},
        {"time": "19:18:10", "event": "Cascade spreads — inventory-service reservation timeouts, notification-service delayed"},
        {"time": "19:18:25", "event": "Alert fired: DatabaseConnectionPoolExhausted (10/10, critical)"},
        {"time": "19:19:00", "event": "Alert fired: RevenueImpactHigh ($8,400/hr estimated loss)"},
        {"time": "19:20:30", "event": "Alert fired: CascadingFailureDetected (4 services affected)"},
        {"time": "19:21:10", "event": "On-call engineer acknowledged — investigating"},
        {"time": "19:21:25", "event": "Root cause identified: db_pool_max changed from 100 to 10 in D-4721"},
        {"time": "19:21:35", "event": "Rollback initiated — D-4722 targeting payment-service v2.8.0"},
        {"time": "19:21:55", "event": "Rollback complete — payment-service v2.8.0 live with db_pool_max=100"},
        {"time": "19:22:05", "event": "Health check passed — pool active=8, idle=92"},
        {"time": "19:22:15", "event": "Circuit breaker CLOSED — normal operation resumed"},
        {"time": "19:23:00", "event": "All alerts resolved — services fully recovered"},
        {"time": "19:23:25", "event": "Incident resolved — TTD: 6 min, TTR: 8 min, impact: ~$1,400 loss, 47 failed orders"},
    ],
    "business_impact": {
        "revenue_loss": "$1,400 estimated",
        "failed_orders": 47,
        "affected_services": 4,
        "services_list": ["payment-service", "order-service", "inventory-service", "notification-service"],
        "customer_impact": "Payment failures, delayed order confirmations, stuck inventory reservations",
        "time_to_detect": "6 minutes",
        "time_to_resolve": "8 minutes",
    },
    "root_cause": {
        "category": "Configuration change — database connection pool",
        "deployment": "D-4721 (payment-service v2.8.1)",
        "change_id": "PAY-88",
        "what_changed": "db_pool_max reduced from 100 to 10 connections",
        "why_it_failed": "10 connections insufficient for peak Friday evening traffic (~50 concurrent payment requests). Pool exhausted within 3 minutes, triggering circuit breaker and cascading failures to dependent services.",
        "contributing_factors": [
            "No pre-deployment validation of connection pool sizing against traffic baselines",
            "Rolling deployment strategy completed before pool saturation was detectable",
            "No canary deployment gate checking pool utilization metrics",
        ],
    },
    "remediation_steps": [
        "1. Immediate: Roll back D-4721 to restore db_pool_max=100 (DONE — D-4722)",
        "2. Short-term: Add pre-deployment gate checking db_pool_max >= 50 for payment-service",
        "3. Short-term: Add Grafana alert for db_pool_active/db_pool_max > 80% sustained 60s",
        "4. Medium-term: Implement canary deployment with automatic rollback on pool saturation",
        "5. Long-term: Move connection pool config to centralized config service with change review",
    ],
    "lessons_learned": [
        "Configuration changes (especially resource pool sizing) need the same review rigor as code changes",
        "Connection pool reduction by 90% should have been flagged by automated policy checks",
        "Cascading failure detection took 5+ minutes — need faster blast-radius alerting",
        "Circuit breaker pattern worked correctly but could not compensate for systemic pool exhaustion",
    ],
    "resolution": "Rolled back to v2.8.0 (D-4722), restoring db_pool_max=100. Services recovered within 2 minutes of rollback.",
}


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": SERVICE,
        "mode": RCA_MODE,
        "log_filter_severity": LOG_FILTER_SEVERITY,
        "rca_output_format": RCA_OUTPUT_FORMAT,
        "model": DEFAULT_MODEL,
        "logs_in_memory": len(_LOG_STORE),
        "datasets_dir": DATASETS_DIR,
    }


@app.get("/config")
def config():
    return {
        "service": SERVICE,
        "mode": RCA_MODE,
        "model": DEFAULT_MODEL,
        "log_filter_severity": LOG_FILTER_SEVERITY,
        "rca_output_format": RCA_OUTPUT_FORMAT,
        "logs_in_memory": len(_LOG_STORE),
        "rca_cache_size": len(_RCA_CACHE),
        "datasets_dir": DATASETS_DIR,
    }


@app.post("/ingest-logs", response_model=IngestResponse)
def ingest_logs():
    global _LOG_STORE
    _LOG_STORE = []
    _RCA_CACHE.clear()

    datasets_path = Path(DATASETS_DIR)
    if not datasets_path.exists():
        raise HTTPException(status_code=404, detail=f"Datasets directory not found: {DATASETS_DIR}")

    files_processed = 0
    total_lines = 0
    lines_parsed = 0
    lines_skipped = 0
    severity_counts: Dict[str, int] = {}

    for log_file in sorted(datasets_path.glob("*")):
        if not log_file.is_file():
            continue

        files_processed += 1
        file_name = log_file.name

        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total_lines += 1

                parsed = None
                if "nginx_access" in file_name:
                    parsed = _parse_nginx_access_line(line)
                elif "nginx_error" in file_name:
                    parsed = _parse_nginx_error_line(line)
                elif "smartdine_app" in file_name or "app" in file_name:
                    parsed = _parse_app_log_line(line)

                if parsed:
                    _LOG_STORE.append(parsed)
                    lines_parsed += 1
                    sev = parsed.get("severity", "UNKNOWN")
                    severity_counts[sev] = severity_counts.get(sev, 0) + 1
                    LOG_INGEST.labels(service=SERVICE, source=parsed.get("source", "unknown")).inc()
                else:
                    lines_skipped += 1

    LOGS_INGESTED.labels(service=SERVICE).set(len(_LOG_STORE))
    for sev, count in severity_counts.items():
        LOGS_BY_SEVERITY.labels(service=SERVICE, severity=sev).set(count)

    logger.info(f"Ingested {lines_parsed} log lines from {files_processed} files ({lines_skipped} skipped)")
    return IngestResponse(
        ok=True,
        files_processed=files_processed,
        total_lines=total_lines,
        lines_parsed=lines_parsed,
        lines_skipped=lines_skipped,
        severity_breakdown=severity_counts,
    )


@app.post("/filter-logs", response_model=FilterResponse)
def filter_logs(req: FilterRequest = FilterRequest()):
    if not _LOG_STORE:
        raise HTTPException(status_code=400, detail="No logs ingested yet. Run POST /ingest-logs first.")

    severity_filter = (req.severity or LOG_FILTER_SEVERITY).upper()

    if severity_filter == "ALL":
        filtered = _LOG_STORE
    else:
        allowed = set(s.strip() for s in severity_filter.split(","))
        filtered = [log for log in _LOG_STORE if log.get("severity", "").upper() in allowed]

    severity_counts: Dict[str, int] = {}
    for log in filtered:
        sev = log.get("severity", "UNKNOWN")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    LOG_FILTER.labels(service=SERVICE, severity=severity_filter).inc()

    sample = filtered[:10] if len(filtered) > 10 else filtered
    sample_clean = []
    for log in sample:
        entry = {k: v for k, v in log.items() if k != "raw"}
        sample_clean.append(entry)

    logger.info(f"Filtered logs: {severity_filter} → {len(filtered)}/{len(_LOG_STORE)} lines")
    return FilterResponse(
        filter_applied=severity_filter,
        total_logs=len(_LOG_STORE),
        filtered_count=len(filtered),
        severity_breakdown=severity_counts,
        sample_logs=sample_clean,
    )


@app.post("/generate-rca", response_model=RCAResponse)
async def generate_rca(req: RCARequest = RCARequest()):
    if not _LOG_STORE:
        raise HTTPException(status_code=400, detail="No logs ingested yet. Run POST /ingest-logs first.")

    t0 = time.perf_counter()

    severity_filter = (req.severity_filter or LOG_FILTER_SEVERITY).upper()
    output_format = (req.format or RCA_OUTPUT_FORMAT).lower()
    is_mock = (RCA_MODE == "mock")

    if severity_filter == "ALL":
        filtered = _LOG_STORE
    else:
        allowed = set(s.strip() for s in severity_filter.split(","))
        filtered = [log for log in _LOG_STORE if log.get("severity", "").upper() in allowed]

    cache_key = f"{severity_filter}|{output_format}|{len(filtered)}"
    if cache_key in _RCA_CACHE:
        cached = _RCA_CACHE[cache_key]
        latency_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(f"RCA cache hit: {cache_key}")
        return RCAResponse(
            format=output_format,
            mode="mock (cached)" if is_mock else "llm (cached)",
            model=DEFAULT_MODEL if not is_mock else f"mock ({DEFAULT_MODEL})",
            logs_analyzed=len(filtered),
            severity_filter=severity_filter,
            rca=cached,
            tokens_used={"prompt": 0, "completion": 0, "total": 0},
            latency_ms=round(latency_ms, 1),
        )

    if is_mock:
        time.sleep(random.uniform(0.1, 0.4))

        if output_format == "detailed":
            rca = MOCK_RCA_DETAILED
        else:
            rca = MOCK_RCA_SUMMARY

        log_context = "\n".join(log.get("raw", "") for log in filtered[:50])
        prompt_tokens = estimate_tokens(log_context)
        completion_tokens = estimate_tokens(json.dumps(rca))
    else:
        log_context = "\n".join(log.get("raw", "") for log in filtered[:100])

        format_instruction = ""
        if output_format == "detailed":
            format_instruction = (
                "Provide a DETAILED RCA with: incident_summary, timeline (list of time+event), "
                "business_impact (revenue_loss, failed_orders, affected_services, time_to_detect, time_to_resolve), "
                "root_cause (category, deployment, change_id, what_changed, why_it_failed, contributing_factors), "
                "remediation_steps (numbered list), lessons_learned (list), resolution."
            )
        else:
            format_instruction = (
                "Provide a SUMMARY RCA with: incident_summary, root_cause, impact, resolution. "
                "Keep each field to 1-3 sentences."
            )

        prompt = (
            f"You are an AIOps incident analyst. Analyze these logs and produce a structured Root Cause Analysis.\n\n"
            f"{format_instruction}\n\n"
            f"Return valid JSON only.\n\n"
            f"LOGS:\n{log_context}"
        )

        rca_result = await _call_openai(prompt, DEFAULT_MODEL)
        rca = json.loads(rca_result[0]) if rca_result else MOCK_RCA_SUMMARY
        prompt_tokens = rca_result[1]
        completion_tokens = rca_result[2]

    LLM_TOKENS.labels(service=SERVICE, model=DEFAULT_MODEL if not is_mock else "mock", direction="prompt").inc(prompt_tokens)
    LLM_TOKENS.labels(service=SERVICE, model=DEFAULT_MODEL if not is_mock else "mock", direction="completion").inc(completion_tokens)

    latency_ms = (time.perf_counter() - t0) * 1000.0
    RCA_GENERATED.labels(service=SERVICE, format=output_format, mode="mock" if is_mock else "llm").inc()
    RCA_LATENCY.labels(service=SERVICE, format=output_format).observe(latency_ms / 1000.0)

    _RCA_CACHE[cache_key] = rca
    RCA_CACHE_SIZE.labels(service=SERVICE).set(len(_RCA_CACHE))

    logger.info(f"RCA generated: format={output_format}, mode={'mock' if is_mock else 'llm'}, logs={len(filtered)}, latency={latency_ms:.0f}ms")
    return RCAResponse(
        format=output_format,
        mode="mock" if is_mock else "llm",
        model=DEFAULT_MODEL if not is_mock else f"mock ({DEFAULT_MODEL})",
        logs_analyzed=len(filtered),
        severity_filter=severity_filter,
        rca=rca,
        tokens_used={"prompt": prompt_tokens, "completion": completion_tokens, "total": prompt_tokens + completion_tokens},
        latency_ms=round(latency_ms, 1),
    )


async def _call_openai(prompt: str, model: str):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY not set. Set it in .env and restart containers, or use RCA_MODE=mock.",
        )

    import openai

    client = openai.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are an AIOps incident analyst. Produce structured JSON RCA reports from log data."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    content = resp.choices[0].message.content or "{}"
    usage = getattr(resp, "usage", None)
    prompt_tokens = usage.prompt_tokens if usage else estimate_tokens(prompt)
    completion_tokens = usage.completion_tokens if usage else estimate_tokens(content)

    return content, prompt_tokens, completion_tokens


@app.post("/cache/clear")
def cache_clear():
    count = len(_RCA_CACHE)
    _RCA_CACHE.clear()
    RCA_CACHE_SIZE.labels(service=SERVICE).set(0)
    logger.info(f"RCA cache cleared: {count} entries removed")
    return {"ok": True, "cleared": count}


@app.get("/cache/stats")
def cache_stats():
    return {
        "cache_size": len(_RCA_CACHE),
        "cache_keys": list(_RCA_CACHE.keys()),
    }


@app.get("/log-stats")
def log_stats():
    if not _LOG_STORE:
        return {"logs_in_memory": 0, "message": "No logs ingested yet. Run POST /ingest-logs first."}

    severity_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    service_counts: Dict[str, int] = {}

    for log in _LOG_STORE:
        sev = log.get("severity", "UNKNOWN")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        src = log.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
        svc = log.get("service", log.get("source", "unknown"))
        service_counts[svc] = service_counts.get(svc, 0) + 1

    return {
        "logs_in_memory": len(_LOG_STORE),
        "severity_breakdown": severity_counts,
        "source_breakdown": source_counts,
        "service_breakdown": service_counts,
    }


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


FILTER_SEVERITY_GAUGE.labels(service=SERVICE, setting=LOG_FILTER_SEVERITY).set(
    1 if LOG_FILTER_SEVERITY == "ALL" else 2 if LOG_FILTER_SEVERITY == "ERROR" else 3 if LOG_FILTER_SEVERITY == "WARN" else 4
)
RCA_FORMAT_GAUGE.labels(service=SERVICE, format=RCA_OUTPUT_FORMAT).set(1 if RCA_OUTPUT_FORMAT == "summary" else 2)

logger.info(
    f"RCA Gateway started: mode={RCA_MODE}, model={DEFAULT_MODEL}, "
    f"filter={LOG_FILTER_SEVERITY}, format={RCA_OUTPUT_FORMAT}, datasets={DATASETS_DIR}"
)
