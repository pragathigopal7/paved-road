"""Publish synthetic order events for the local stack.

Each message is sent inside a producer span and carries a W3C traceparent header, so
Jaeger shows one trace from producer to consumer. A small share of messages is
deliberately malformed to exercise the consumer's dead letter path.
"""

import json
import os
import random
import time
import uuid

from confluent_kafka import Producer
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind


def main() -> None:
    rate = float(os.environ.get("RATE_PER_SECOND", "20"))
    malformed_ratio = float(os.environ.get("MALFORMED_RATIO", "0.02"))
    topic = os.environ.get("KAFKA_TOPIC", "orders")

    provider = TracerProvider(resource=Resource.create({"service.name": "order-producer"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("order-producer")

    producer = Producer(
        {"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"], "enable.idempotence": True}
    )
    rng = random.Random()  # noqa: S311 (synthetic data, not security sensitive)
    while True:
        attrs = {"messaging.system": "kafka", "messaging.destination.name": topic}
        span_name = f"send {topic}"
        with tracer.start_as_current_span(span_name, kind=SpanKind.PRODUCER, attributes=attrs):
            headers: dict[str, str] = {}
            propagate.inject(headers)
            if rng.random() < malformed_ratio:
                value = b"{not json"
            else:
                order = {
                    "id": str(uuid.uuid4()),
                    "amount_cents": rng.randint(100, 50_000),
                    "ts": time.time(),
                }
                value = json.dumps(order).encode()
            producer.produce(
                topic,
                key=str(uuid.uuid4()).encode(),
                value=value,
                headers=[(k, v.encode()) for k, v in headers.items()],
            )
        producer.poll(0)
        time.sleep(1 / rate)


if __name__ == "__main__":
    main()
