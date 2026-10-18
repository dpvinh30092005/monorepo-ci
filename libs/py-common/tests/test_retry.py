import pytest

from py_common import retry


def test_returns_after_transient_failures():
    calls = []

    @retry(attempts=3, sleep=lambda _: None)
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError
        return "ok"

    assert flaky() == "ok"
    assert len(calls) == 3


def test_reraises_when_exhausted():
    @retry(attempts=2, sleep=lambda _: None)
    def broken():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        broken()


def test_does_not_retry_unlisted_exceptions():
    calls = []

    @retry(attempts=5, exceptions=(ConnectionError,), sleep=lambda _: None)
    def bad():
        calls.append(1)
        raise ValueError

    with pytest.raises(ValueError):
        bad()
    assert len(calls) == 1


def test_rejects_invalid_attempts():
    with pytest.raises(ValueError):
        retry(attempts=0)
