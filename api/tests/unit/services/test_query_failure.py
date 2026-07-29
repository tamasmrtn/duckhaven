from api.services.query_failure import (
    OTHER,
    UNKNOWN,
    classify_failure,
    is_admission_reject,
)


def test_admission_rejects_match_the_agents_exact_strings():
    # These two are values the agent chooses (executor/admission.py), not prose.
    assert classify_failure("queue full") == "queue_full"
    assert classify_failure("queued timeout") == "queued_timeout"
    assert is_admission_reject("queue_full")
    assert is_admission_reject("queued_timeout")


def test_admission_reject_matching_is_exact_not_substring():
    """A DuckDB error merely mentioning a full queue is not an admission reject.

    Guards the Prometheus counter's meaning: duckhaven_query_queue_rejected must
    count saturation, not any failure whose text happens to contain the phrase.
    """
    reason = classify_failure("Binder Error: table 'queue full' does not exist")
    assert reason == OTHER
    assert not is_admission_reject(reason)


def test_control_plane_written_errors_classify():
    assert classify_failure("No compute became available for this run.") == "no_compute"
    assert classify_failure("dispatch failed after provisioning") == "dispatch_failed"


def test_engine_errors_classify():
    assert classify_failure("Out of Memory Error: could not allocate block") == "out_of_memory"
    assert classify_failure("timeout") == "timeout"


def test_empty_error_is_unknown_and_unmatched_is_other():
    assert classify_failure(None) == UNKNOWN
    assert classify_failure("   ") == UNKNOWN
    assert classify_failure("Catalog Error: Table with name t does not exist!") == OTHER
    assert not is_admission_reject(OTHER)


def test_classification_is_case_insensitive():
    assert classify_failure("QUEUE FULL") == "queue_full"
    assert classify_failure("Out Of Memory") == "out_of_memory"
