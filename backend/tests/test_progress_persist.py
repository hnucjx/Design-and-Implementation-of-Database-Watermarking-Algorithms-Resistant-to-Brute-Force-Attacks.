from app.progress_persist import ProgressPersistGate


def test_progress_persist_gate_allows_first_and_finished() -> None:
    gate = ProgressPersistGate(min_interval_seconds=1.0, min_progress_delta=50.0)
    assert gate.allow(status="downloading", progress=1.0, now=0.0) is True
    assert gate.allow(status="downloading", progress=1.1, now=0.01) is False
    assert gate.allow(status="finished", progress=1.1, now=0.02) is True


def test_progress_persist_gate_allows_interval_or_progress_jump() -> None:
    gate = ProgressPersistGate(min_interval_seconds=0.25, min_progress_delta=0.5)
    assert gate.allow(status="downloading", progress=10.0, now=0.0) is True
    assert gate.allow(status="downloading", progress=10.2, now=0.05) is False
    assert gate.allow(status="downloading", progress=10.8, now=0.06) is True
    assert gate.allow(status="downloading", progress=10.9, now=0.10) is False
    assert gate.allow(status="downloading", progress=11.0, now=0.40) is True
