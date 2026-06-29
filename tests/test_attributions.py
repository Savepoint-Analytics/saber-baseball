"""Compliance attributions must be exact and always present — tested explicitly."""

from src.compliance.attributions import (
    ATTRIBUTIONS,
    RESTRICTED_REDISTRIBUTION,
    Source,
    attribution_block,
    attribution_for,
    stamp_text,
)

RETROSHEET_EXACT = (
    "The information used here was obtained free of charge from and is "
    "copyrighted by Retrosheet. Interested parties may contact Retrosheet at "
    "www.retrosheet.org."
)


def test_every_source_has_attribution():
    for s in Source:
        assert s in ATTRIBUTIONS
        assert ATTRIBUTIONS[s].strip()


def test_retrosheet_notice_is_verbatim():
    # Retrosheet's terms require this exact wording — guard against drift.
    assert attribution_for(Source.RETROSHEET) == RETROSHEET_EXACT


def test_block_dedupes_and_orders():
    block = attribution_block([Source.STATCAST, Source.RETROSHEET, Source.STATCAST])
    # Retrosheet (earlier in enum) should precede Statcast; no dupes.
    assert block.count("Retrosheet") >= 1
    assert block.count("Baseball Savant") == 1
    assert block.index("Retrosheet") < block.index("Baseball Savant")


def test_stamp_text_appends_notice():
    out = stamp_text("Some analysis.", [Source.LAHMAN])
    assert out.startswith("Some analysis.")
    assert "Lahman Baseball Database" in out


def test_restricted_sources_flagged():
    assert Source.STATCAST in RESTRICTED_REDISTRIBUTION
    assert Source.MLB_STATS_API in RESTRICTED_REDISTRIBUTION
    assert Source.LAHMAN not in RESTRICTED_REDISTRIBUTION
