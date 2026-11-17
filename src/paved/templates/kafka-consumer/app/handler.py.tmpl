"""Business logic. Replace handle() with your own.

Keep this module free of Kafka and telemetry code so it stays easy to unit test.
Raise PermanentError for messages that can never succeed; they skip retries and go
straight to the dead letter topic. Any other exception is retried.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


class PermanentError(Exception):
    """The message is malformed or invalid; retrying will not help."""


def handle(key: bytes | None, value: bytes | None) -> None:
    if value is None:
        raise PermanentError("tombstone or empty message")
    try:
        event = json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PermanentError(f"value is not JSON: {exc}") from exc
    if not isinstance(event, dict) or "id" not in event:
        raise PermanentError("event is missing required field 'id'")

    log.info("event processed", extra={"event_id": event["id"]})
