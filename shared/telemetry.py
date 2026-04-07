import time
from typing import Callable

from prometheus_client import Counter, Histogram

REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)

LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency (seconds)",
    ["service", "method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13),
)

LOG_INGEST = Counter(
    "logs_ingested_total",
    "Total log lines ingested",
    ["service", "source"],
)

LOG_FILTER = Counter(
    "logs_filtered_total",
    "Total log filter operations",
    ["service", "severity"],
)

RCA_GENERATED = Counter(
    "rca_generated_total",
    "Total RCA reports generated",
    ["service", "format", "mode"],
)

RCA_LATENCY = Histogram(
    "rca_generation_seconds",
    "RCA generation latency (seconds)",
    ["service", "format"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)

LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Total token usage (estimated)",
    ["service", "model", "direction"],
)


def prom_middleware(service_name: str):
    async def middleware(request, call_next: Callable):
        path = request.url.path
        method = request.method
        start = time.perf_counter()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            dur = time.perf_counter() - start
            REQUESTS.labels(service=service_name, method=method, path=path, status=status).inc()
            LATENCY.labels(service=service_name, method=method, path=path).observe(dur)

    return middleware
