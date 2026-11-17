"""Observability defaults every paved road service gets for free.

* Logs: one JSON object per line, with trace_id and span_id of the active span.
* Metrics: RED metrics plus consumer lag, served on /metrics for Prometheus.
* Traces: OpenTelemetry SDK, exported over OTLP/HTTP to the cluster collector.
* Health: /healthz (poll loop is alive) and /readyz (joined the consumer group).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

_STD_ATTRS = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {"message", "asctime"}


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.messages = Counter(
            "messages_processed", "Messages handled, by outcome", ["topic", "outcome"], registry=r
        )
        self.duration = Histogram(
            "message_processing_seconds",
            "Time to handle one message, including retries",
            ["topic"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=r,
        )
        self.dead_letters = Counter(
            "dead_letter_messages",
            "Messages sent to the dead letter topic",
            ["topic", "reason"],
            registry=r,
        )
        self.lag = Gauge(
            "kafka_consumer_lag",
            "Messages between the committed position and the partition high watermark",
            ["topic", "partition", "consumergroup"],
            registry=r,
        )
        self.assigned = Gauge(
            "kafka_assigned_partitions", "Partitions currently assigned to this pod", registry=r
        )
        self.last_poll = Gauge(
            "consumer_last_poll_timestamp_seconds", "Unix time of the last poll() call", registry=r
        )


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "service": self.service,
        }
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            doc["trace_id"] = format(ctx.trace_id, "032x")
            doc["span_id"] = format(ctx.span_id, "016x")
        for key, value in vars(record).items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                doc[key] = value
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def setup_logging(service: str, level: str = "info", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def setup_tracing(service: str, exporter: SpanExporter | None = None) -> TracerProvider:
    """Build a tracer provider. Resource.create also merges OTEL_RESOURCE_ATTRIBUTES."""
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    if exporter is None and os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter()
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


class Health:
    def __init__(self, stall_after_seconds: float = 60.0):
        self.stall_after = stall_after_seconds
        self.ready = False
        self._last_beat = time.monotonic()

    def beat(self) -> None:
        self._last_beat = time.monotonic()

    def live(self) -> bool:
        return time.monotonic() - self._last_beat < self.stall_after


def serve_http(port: int, metrics: Metrics, health: Health) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            body, ctype = b"", "text/plain"
            if self.path == "/metrics":
                body, ctype, code = generate_latest(metrics.registry), CONTENT_TYPE_LATEST, 200
            elif self.path == "/healthz":
                code = 200 if health.live() else 503
            elif self.path == "/readyz":
                code = 200 if health.ready else 503
            else:
                code = 404
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return  # keep probe and scrape noise out of application logs

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 (pod network only)
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    return server
