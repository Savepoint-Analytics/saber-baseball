"""Run expectancy (RE24) and Win Probability Added (WPA) — Phase 2 (stub).

These tables are both **features** for the prediction models and **explanatory tools**
for "how games are won" (PROJECT_CONTEXT §7). They are built from Retrosheet event
data landed in bronze. Implement in Phase 2.

Plan:
  1. Read Retrosheet events (24 base/out states × runs scored to end of inning).
  2. Build the RE24 matrix: expected runs to end of inning per base/out state.
  3. Derive run value of each event (delta-RE + runs on play).
  4. Build WPA from a win-probability table keyed on inning/score/base/out.
"""

from __future__ import annotations

import pandas as pd


def build_re24(events: pd.DataFrame) -> pd.DataFrame:
    """Build the 24-state run-expectancy matrix from Retrosheet events. (Phase 2)"""
    raise NotImplementedError("RE24 construction lands in Phase 2.")


def build_wpa_table(events: pd.DataFrame) -> pd.DataFrame:
    """Build the win-probability lookup table for WPA. (Phase 2)"""
    raise NotImplementedError("WPA table construction lands in Phase 2.")
