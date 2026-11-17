"""Consumer loop tests against in memory fakes; no broker needed."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from opentelemetry import propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import Config
from app.handler import PermanentError
from app.main import ConsumerApp
from app.observability import Health, Metrics

CFG = Config("svc", "unused:9092", "orders", "svc", "orders.dlq", max_attempts=3)


@dataclass
class Msg:
    _value: bytes | None
    _offset: int = 0
    _headers: list = field(default_factory=list)

    def topic(self):
        return "orders"

    def partition(self):
        return 0

    def offset(self):
        return self._offset

    def key(self):
        return b"k"

    def value(self):
        return self._value

    def headers(self):
        return self._headers

    def error(self):
        return None


@dataclass
class TP:
    topic: str
    partition: int
    offset: int = -1001


class FakeConsumer:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.committed: list[int] = []
        self.closed = False
        self.stop: threading.Event | None = None

    def subscribe(self, topics, on_assign, on_revoke):
        on_assign(self, [TP("orders", 0)])

    def poll(self, timeout):
        if self.messages:
            return self.messages.pop(0)
        self.stop.set()
        return None

    def commit(self, message, asynchronous):
        self.committed.append(message.offset())

    def assignment(self):
        return [TP("orders", 0)]

    def position(self, tps):
        return [TP("orders", 0, 40)]

    def get_watermark_offsets(self, tp, timeout, cached):
        return (0, 100)

    def close(self):
        self.closed = True


class FakeProducer:
    def __init__(self):
        self.sent = []

    def produce(self, topic, key, value, headers):
        self.sent.append((topic, value, dict(headers)))

    def flush(self, timeout):
        return 0


def make_app(consumer, handler):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = ConsumerApp(
        CFG,
        consumer,
        FakeProducer(),
        Metrics(),
        Health(),
        provider.get_tracer("t"),
        handler=handler,
        sleep=lambda s: None,
    )
    return app, exporter


def sample(app, name, **labels):
    return app.metrics.registry.get_sample_value(name, labels or None)


def test_success_commits_and_counts():
    consumer = FakeConsumer([Msg(b'{"id": 1}', 7)])
    app, _ = make_app(consumer, lambda k, v: None)
    stop = threading.Event()
    consumer.stop = stop
    app.run(stop)
    assert consumer.committed == [7]
    assert consumer.closed
    assert app.health.ready
    assert sample(app, "messages_processed_total", topic="orders", outcome="ok") == 1


def test_permanent_error_goes_straight_to_dlq():
    calls = []

    def bad(k, v):
        calls.append(1)
        raise PermanentError("nope")

    consumer = FakeConsumer()
    app, spans = make_app(consumer, bad)
    assert app.process(Msg(b"x", 3)) == "dead_letter"
    assert len(calls) == 1
    topic, _, headers = app.producer.sent[0]
    assert topic == "orders.dlq"
    assert headers["dlq.reason"] == b"permanent"
    assert headers["dlq.source"] == b"orders/0/3"
    assert consumer.committed == [3]
    assert spans.get_finished_spans()[0].status.is_ok is False


def test_transient_error_retries_then_dead_letters():
    calls = []

    def flaky(k, v):
        calls.append(1)
        raise TimeoutError("downstream timeout")

    app, _ = make_app(FakeConsumer(), flaky)
    assert app.process(Msg(b'{"id": 1}')) == "dead_letter"
    assert len(calls) == CFG.max_attempts
    dead = sample(app, "dead_letter_messages_total", topic="orders", reason="retries_exhausted")
    assert dead == 1


def test_trace_context_is_continued_from_headers():
    carrier = {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"}
    app, spans = make_app(FakeConsumer(), lambda k, v: None)
    app.process(Msg(b'{"id": 1}', _headers=[(k, v.encode()) for k, v in carrier.items()]))
    span = spans.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert span.attributes["messaging.destination.name"] == "orders"
    assert propagate.extract(carrier)  # sanity: W3C propagator is the default


def test_lag_is_high_watermark_minus_position():
    app, _ = make_app(FakeConsumer(), lambda k, v: None)
    app.update_lag()
    lag = sample(app, "kafka_consumer_lag", topic="orders", partition="0", consumergroup="svc")
    assert lag == 60
    assert sample(app, "kafka_assigned_partitions") == 1
