"""Processes batches of jobs, retrying transient failures."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from py_common import retry


@dataclass
class BatchResult:
    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


def process_batch(
    job_ids: Iterable[str],
    handler: Callable[[str], None],
    attempts: int = 3,
) -> BatchResult:
    """Run `handler` for every job; one bad job never stops the batch."""
    result = BatchResult()
    safe_handler = retry(attempts=attempts, exceptions=(ConnectionError, TimeoutError))(handler)
    for job_id in job_ids:
        try:
            safe_handler(job_id)
        except Exception as exc:  # noqa: BLE001 - record and keep going
            result.failed[job_id] = f"{type(exc).__name__}: {exc}"
        else:
            result.succeeded.append(job_id)
    return result
