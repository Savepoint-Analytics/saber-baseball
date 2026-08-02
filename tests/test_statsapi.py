"""Season windowing and multi-season backfill logic (no network — `ingest` is stubbed).

The backfill target is what makes a multi-season history reproducible from a clean
checkout, so its ordering, range handling, and failure semantics are worth pinning.
"""

import pytest

from src.ingestion import statsapi


def test_season_window_spans_openers_through_world_series():
    start, end = statsapi.season_window(2025)
    # Must start before the 2025-03-18 Tokyo opener and end after any World Series.
    assert start == "2025-03-01"
    assert end == "2025-11-30"


def test_backfill_covers_every_season_in_order(monkeypatch):
    seen: list[str] = []

    def fake_ingest(start: str, end: str) -> str:
        seen.append(start[:4])
        return f"key-{start[:4]}"

    monkeypatch.setattr(statsapi, "ingest", fake_ingest)

    keys = statsapi.backfill(2015, 2018)

    assert seen == ["2015", "2016", "2017", "2018"]
    assert keys == ["key-2015", "key-2016", "key-2017", "key-2018"]


def test_backfill_single_season_range(monkeypatch):
    monkeypatch.setattr(statsapi, "ingest", lambda start, end: f"key-{start[:4]}")
    assert statsapi.backfill(2020, 2020) == ["key-2020"]


def test_backfill_rejects_inverted_range():
    with pytest.raises(ValueError, match="precedes"):
        statsapi.backfill(2025, 2015)


def test_backfill_continues_past_a_failed_season_then_raises(monkeypatch):
    """One bad season must not discard the seasons that pulled cleanly."""
    attempted: list[str] = []

    def flaky_ingest(start: str, end: str) -> str:
        season = start[:4]
        attempted.append(season)
        if season == "2016":
            raise RuntimeError("upstream 503")
        return f"key-{season}"

    monkeypatch.setattr(statsapi, "ingest", flaky_ingest)

    with pytest.raises(RuntimeError, match=r"2016"):
        statsapi.backfill(2015, 2017)

    # 2017 was still attempted after 2016 blew up.
    assert attempted == ["2015", "2016", "2017"]
