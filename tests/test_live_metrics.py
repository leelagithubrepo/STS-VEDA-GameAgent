from datetime import datetime, timedelta

from scripts.veda_live_metrics import summarize


def test_summarize_observation_cadence():
    start = datetime(2026, 9, 26, 13, 0, 0)
    result = summarize([start, start + timedelta(seconds=30), start + timedelta(seconds=90)])
    assert result["observations"] == 3
    assert result["elapsed_minutes"] == 1.5
    assert result["median_interval_seconds"] == 45


def test_summarize_empty_directory():
    assert summarize([])["observations"] == 0
