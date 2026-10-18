from worker.processor import process_batch


def test_all_jobs_succeed():
    seen = []
    result = process_batch(["a", "b"], seen.append)
    assert result.succeeded == ["a", "b"]
    assert result.failed == {}
    assert seen == ["a", "b"]


def test_transient_errors_are_retried():
    attempts = {"a": 0}

    def handler(job_id):
        attempts[job_id] += 1
        if attempts[job_id] < 2:
            raise TimeoutError("slow")

    result = process_batch(["a"], handler)
    assert result.succeeded == ["a"]
    assert attempts["a"] == 2


def test_permanent_error_is_recorded_and_batch_continues():
    def handler(job_id):
        if job_id == "bad":
            raise ValueError("corrupt payload")

    result = process_batch(["bad", "good"], handler)
    assert result.succeeded == ["good"]
    assert result.failed == {"bad": "ValueError: corrupt payload"}
