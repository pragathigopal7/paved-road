"""Consumer loop with at least once delivery, bounded retries and a dead letter topic.

Offsets are committed only after a message is handled or dead lettered, so a crash
replays work instead of losing it. Handlers therefore need to be idempotent.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from app.config import Config
from app.handler import PermanentError, handle
from app.observability import Health, Metrics, serve_http, setup_logging, setup_tracing

log = logging.getLogger("orders-consumer")

LAG_INTERVAL_SECONDS = 15.0
OFFSET_INVALID = -1001  # librdkafka's "no position yet"


def _headers(msg: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in msg.headers() or []:
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        out[key] = value
    return out


class ConsumerApp:
    def __init__(
        self,
        cfg: Config,
        consumer: Any,
        producer: Any,
        metrics: Metrics,
        health: Health,
        tracer: trace.Tracer,
        handler: Callable[[bytes | None, bytes | None], None] = handle,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg = cfg
        self.consumer = consumer
        self.producer = producer
        self.metrics = metrics
        self.health = health
        self.tracer = tracer
        self.handler = handler
        self.sleep = sleep

    # Rebalance callbacks -------------------------------------------------------------

    def on_assign(self, consumer: Any, partitions: list[Any]) -> None:
        # Ready once we have joined the group, even with zero partitions, so rollouts
        # do not stall when there are more replicas than partitions.
        self.health.ready = True
        log.info("partitions assigned", extra={"partitions": [p.partition for p in partitions]})

    def on_revoke(self, consumer: Any, partitions: list[Any]) -> None:
        for p in partitions:
            # KeyError: never measured, so nothing to clear.
            with contextlib.suppress(KeyError):
                self.metrics.lag.remove(p.topic, str(p.partition), self.cfg.group_id)
        log.info("partitions revoked", extra={"partitions": [p.partition for p in partitions]})

    # Message handling ------------------------------------------------------------------

    def process(self, msg: Any) -> str:
        topic, partition, offset = msg.topic(), msg.partition(), msg.offset()
        parent = propagate.extract(_headers(msg))
        attrs = {
            "messaging.system": "kafka",
            "messaging.operation.type": "process",
            "messaging.destination.name": topic,
            "messaging.consumer.group.name": self.cfg.group_id,
            "messaging.destination.partition.id": str(partition),
            "messaging.kafka.offset": offset,
        }
        start = time.perf_counter()
        with self.tracer.start_as_current_span(
            f"process {topic}", context=parent, kind=SpanKind.CONSUMER, attributes=attrs
        ) as span:
            outcome = "ok"
            for attempt in range(1, self.cfg.max_attempts + 1):
                try:
                    self.handler(msg.key(), msg.value())
                    break
                except PermanentError as exc:
                    outcome = self.dead_letter(msg, "permanent", exc)
                    break
                except Exception as exc:
                    if attempt == self.cfg.max_attempts:
                        outcome = self.dead_letter(msg, "retries_exhausted", exc)
                        break
                    log.warning(
                        "handler failed, retrying",
                        extra={"attempt": attempt, "error": str(exc), "offset": offset},
                    )
                    self.sleep(min(0.2 * 2**attempt, 5.0))
            span.set_attribute("app.outcome", outcome)
            if outcome != "ok":
                span.set_status(Status(StatusCode.ERROR, outcome))

        self.metrics.duration.labels(topic).observe(time.perf_counter() - start)
        self.metrics.messages.labels(topic, outcome).inc()
        self.consumer.commit(message=msg, asynchronous=False)
        return outcome

    def dead_letter(self, msg: Any, reason: str, exc: Exception) -> str:
        headers = list(msg.headers() or [])
        headers += [
            ("dlq.reason", reason.encode()),
            ("dlq.error", str(exc)[:500].encode()),
            ("dlq.source", f"{msg.topic()}/{msg.partition()}/{msg.offset()}".encode()),
        ]
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        headers += [(k, v.encode()) for k, v in carrier.items()]
        self.producer.produce(self.cfg.dlq_topic, key=msg.key(), value=msg.value(), headers=headers)
        remaining = self.producer.flush(10)
        if remaining:
            # Do not commit an offset we failed to park; crash and replay instead.
            raise RuntimeError(f"dead letter produce did not complete ({remaining} pending)")
        self.metrics.dead_letters.labels(msg.topic(), reason).inc()
        log.error(
            "message dead lettered",
            extra={
                "reason": reason,
                "error": str(exc),
                "partition": msg.partition(),
                "offset": msg.offset(),
            },
        )
        return "dead_letter"

    def update_lag(self) -> None:
        assignment = list(self.consumer.assignment())
        self.metrics.assigned.set(len(assignment))
        if not assignment:
            return
        positions = {(p.topic, p.partition): p.offset for p in self.consumer.position(assignment)}
        for tp in assignment:
            low, high = self.consumer.get_watermark_offsets(tp, timeout=5, cached=False)
            pos = positions.get((tp.topic, tp.partition), OFFSET_INVALID)
            if pos is None or pos < 0:
                pos = low
            self.metrics.lag.labels(tp.topic, str(tp.partition), self.cfg.group_id).set(
                max(0, high - pos)
            )

    # Main loop -------------------------------------------------------------------------

    def run(self, stop: threading.Event) -> None:
        self.consumer.subscribe(
            [self.cfg.topic], on_assign=self.on_assign, on_revoke=self.on_revoke
        )
        next_lag = 0.0
        try:
            while not stop.is_set():
                msg = self.consumer.poll(1.0)
                self.health.beat()
                self.metrics.last_poll.set(time.time())
                if time.monotonic() >= next_lag:
                    try:
                        self.update_lag()
                    except Exception as exc:  # lag is best effort, never kill the loop
                        log.warning("lag update failed", extra={"error": str(exc)})
                    next_lag = time.monotonic() + LAG_INTERVAL_SECONDS
                if msg is None:
                    continue
                err = msg.error()
                if err is not None:
                    if getattr(err, "fatal", lambda: False)():
                        raise RuntimeError(f"fatal consumer error: {err}")
                    log.warning("consumer error", extra={"error": str(err)})
                    continue
                self.process(msg)
        finally:
            self.consumer.close()  # leave the group now so partitions move immediately


def main() -> None:
    from confluent_kafka import Consumer, Producer

    cfg = Config.from_env()
    setup_logging(cfg.service_name, cfg.log_level, cfg.log_format)
    provider = setup_tracing(cfg.service_name)
    trace.set_tracer_provider(provider)
    metrics, health = Metrics(), Health()
    server = serve_http(cfg.http_port, metrics, health)

    consumer = Consumer(
        {
            "bootstrap.servers": cfg.bootstrap_servers,
            "group.id": cfg.group_id,
            "client.id": socket.gethostname(),
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "partition.assignment.strategy": "cooperative-sticky",
        }
    )
    producer = Producer(
        {"bootstrap.servers": cfg.bootstrap_servers, "enable.idempotence": True, "acks": "all"}
    )

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    app = ConsumerApp(cfg, consumer, producer, metrics, health, provider.get_tracer(__name__))
    log.info("starting", extra={"topic": cfg.topic, "group": cfg.group_id})
    try:
        app.run(stop)
    finally:
        producer.flush(10)
        provider.shutdown()
        server.shutdown()
        log.info("stopped")


if __name__ == "__main__":
    main()
