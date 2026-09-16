"""Streamlit serving app — exploration shell that always shows attributions.

A thin dashboard that (a) confirms the stack is wired and (b) demonstrates the
compliance pattern — every view renders the required source attributions in its
footer via ``src.compliance.attributions``. The real outputs currently live in the
CLIs (``make today``, ``make player-value``); charts over the gold marts are the
next serving step.

Run::

    streamlit run src/serving/app.py
"""

from __future__ import annotations


def main() -> None:
    import streamlit as st

    from src.compliance.attributions import Source, attribution_block
    from src.config import settings

    st.set_page_config(page_title="Savepoint — Baseball Sabermetrics", layout="wide")
    st.title("⚾ Savepoint Analytics — Baseball Sabermetrics")
    st.caption(
        "Reproducible, compliant MLB analytics. Dashboard shell — predictions live in "
        "`make today`, player value cards in `make player-value`; views over the gold "
        "marts arrive next."
    )

    st.subheader("Environment")
    st.write(
        {
            "env": settings.env,
            "storage_backend": settings.storage_backend,
            "bronze_root": settings.bronze_root,
        }
    )

    st.subheader("Roadmap")
    st.markdown(
        "- **Phase 1 (done):** compliant bronze ingestion, dbt marts, Prefect daily chain\n"
        "- **Phase 2 (in progress):** RE24 and WPA per player, linear weights, season value marts\n"
        "- **Phase 3 (done, Elo still the bar):** nine outcome-model families backtested\n"
        "- **Phase 4 (done, calibration caveat):** PA engine, Monte-Carlo season → playoff/WS odds\n"
        "- **Phase 5 (next):** counterfactual player simulation"
    )

    st.divider()
    st.caption(
        attribution_block([Source.RETROSHEET, Source.LAHMAN, Source.STATCAST, Source.CHADWICK])
    )


if __name__ == "__main__":
    main()
