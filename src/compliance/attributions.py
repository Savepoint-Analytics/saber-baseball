"""Canonical data-source attributions, centralized and auto-stamped onto exports.

Why this module exists (see ``CLAUDE.md`` §5 and ``public-data-sources.md``):
Savepoint sells *derived analysis*, and several upstream sources grant commercial
use only on the condition that a specific notice is displayed. Retrosheet in
particular requires its EXACT wording. Centralizing the strings here — and stamping
them automatically onto every report/dashboard/dataset export — makes compliance a
property of the pipeline rather than something a human must remember each time.

Usage::

    from src.compliance.attributions import attribution_block, Source

    notice = attribution_block([Source.RETROSHEET, Source.STATCAST])
    # ... write `notice` into the footer of a report, the title slide of a deck,
    #     or a NOTICE.txt beside an exported dataset.
"""

from __future__ import annotations

from enum import Enum


class Source(str, Enum):
    """Data sources whose attribution may need to appear on a deliverable."""

    RETROSHEET = "retrosheet"
    LAHMAN = "lahman"
    STATCAST = "statcast"  # MLB Advanced Media / Baseball Savant
    MLB_STATS_API = "mlb_stats_api"  # MLBAM
    BBREF = "bbref"  # Sports Reference LLC
    CHADWICK = "chadwick"
    BASEBALL_COMPUTER = "baseball_computer"


# Exact, source-mandated notices. Do NOT paraphrase RETROSHEET — its terms require
# this precise wording to remain displayed.
ATTRIBUTIONS: dict[Source, str] = {
    Source.RETROSHEET: (
        "The information used here was obtained free of charge from and is "
        "copyrighted by Retrosheet. Interested parties may contact Retrosheet at "
        "www.retrosheet.org."
    ),
    Source.LAHMAN: (
        "Data from the Lahman Baseball Database, © 1996–2024 Sean Lahman, "
        "licensed under CC BY-SA 3.0."
    ),
    Source.STATCAST: "Includes data © MLB Advanced Media / Baseball Savant.",
    Source.MLB_STATS_API: "Includes data © MLB Advanced Media.",
    Source.BBREF: "Stats courtesy of Baseball-Reference.com (Sports Reference LLC).",
    Source.CHADWICK: "Player ID crosswalk from the Chadwick Bureau register.",
    Source.BASEBALL_COMPUTER: (
        "Built on baseball.computer (derived from Retrosheet data). "
        + "The information used here was obtained free of charge from and is "
        "copyrighted by Retrosheet. Interested parties may contact Retrosheet at "
        "www.retrosheet.org."
    ),
}

# Sources that carry a hard "do not redistribute the raw feed" condition. Stamping
# attribution is necessary but NOT sufficient for these — the deliverable must be
# derived analysis, never the raw data. Surfaced so callers/tests can assert intent.
RESTRICTED_REDISTRIBUTION: frozenset[Source] = frozenset(
    {Source.STATCAST, Source.MLB_STATS_API, Source.BBREF}
)


def attribution_for(source: Source) -> str:
    """Return the exact attribution string for a single source."""
    return ATTRIBUTIONS[source]


def attribution_block(
    sources: list[Source] | set[Source],
    *,
    header: str = "Data sources & attribution",
) -> str:
    """Build a deduplicated, deterministically ordered attribution block.

    Suitable for a report footer, dashboard "About" panel, or a NOTICE.txt placed
    beside an exported dataset.
    """
    seen: list[Source] = []
    for s in sources:
        if s not in seen:
            seen.append(s)
    # Stable, canonical ordering for reproducible output.
    seen.sort(key=lambda s: list(Source).index(s))

    lines = [header, "-" * len(header)]
    lines.extend(f"• {ATTRIBUTIONS[s]}" for s in seen)
    return "\n".join(lines)


def stamp_text(body: str, sources: list[Source] | set[Source]) -> str:
    """Append the attribution block to a block of text, returning the stamped text."""
    return f"{body.rstrip()}\n\n{attribution_block(sources)}\n"
