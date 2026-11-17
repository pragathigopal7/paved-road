"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    service_name: str
    bootstrap_servers: str
    topic: str
    group_id: str
    dlq_topic: str
    max_attempts: int = 3
    http_port: int = 8080
    log_level: str = "info"
    log_format: str = "json"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Config:
        return cls(
            service_name=env.get("OTEL_SERVICE_NAME", "orders-consumer"),
            bootstrap_servers=env.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            topic=env.get("KAFKA_TOPIC", "orders"),
            group_id=env.get("KAFKA_GROUP_ID", "orders-consumer"),
            dlq_topic=env.get("KAFKA_DLQ_TOPIC", "orders.dlq"),
            max_attempts=int(env.get("MAX_ATTEMPTS", "3")),
            http_port=int(env.get("HTTP_PORT", "8080")),
            log_level=env.get("LOG_LEVEL", "info"),
            log_format=env.get("LOG_FORMAT", "json"),
        )
