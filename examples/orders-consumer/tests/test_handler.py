import pytest

from app.handler import PermanentError, handle


def test_valid_event():
    handle(b"k", b'{"id": "evt-1"}')


@pytest.mark.parametrize("value", [None, b"not json", b"[1, 2]", b'{"no_id": true}'])
def test_invalid_events_are_permanent(value):
    with pytest.raises(PermanentError):
        handle(b"k", value)
