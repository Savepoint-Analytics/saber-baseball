"""Streamlit serving app — exploration shell that always shows attributions.

Phase 1 deliverable: a thin dashboard that (a) confirms the stack is wired and (b)
demonstrates the compliance pattern — every view renders the required source
attributions in its footer via ``src.compliance.attributions``. Charts over real
gold-layer tables land alongside the modeling phases.

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
        "Reproducible, compliant MLB analytics. Phase 1 (ingestion) shell — "
        "prediction, projection, and simulation views arrive in later phases."
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
        "- **Phase 1 (current):** compliant bronze ingestion\n"
        "- **Phase 2:** run expectancy (RE24) & WPA\n"
        "- **Phase 3:** game-outcome prediction (baselines → GBM/Bayesian)\n"
        "- **Phase 4:** Monte-Carlo season → playoff/WS odds\n"
        "- **Phase 5:** counterfactual player simulation"
    )

    st.divider()
    st.caption(
        attribution_block([Source.RETROSHEET, Source.LAHMAN, Source.STATCAST, Source.CHADWICK])
    )


if __name__ == "__main__":
    main()
