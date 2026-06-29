"""Bronze-layer write helpers: land raw pulls as dated Parquet (local or MinIO).

Keys are date-partitioned per ``CLAUDE.md`` §4, e.g.::

    bronze/statcast/season=2025/date=2025-06-24.parquet

A NOTICE.txt carrying the source attribution is written alongside each dataset so
the required notice always travels with the raw landing zone.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from loguru import logger

from src.compliance.attributions import Source, attribution_block
from src.config import settings


def bronze_key(dataset: str, *parts: str) -> str:
    """Compose a partitioned key under the bronze root for ``dataset``."""
    suffix = "/".join([dataset, *parts])
    root = settings.bronze_root
    sep = "" if root.endswith("/") else "/"
    return f"{root}{sep}{suffix}"


def write_parquet(df: pd.DataFrame, key: str, *, sources: list[Source]) -> str:
    """Write a DataFrame to Parquet at ``key`` (local path or ``s3://...``).

    Returns the written key. Also drops a NOTICE.txt next to it with attributions.
    """
    if settings.storage_backend == "minio":
        storage_options = settings.s3_storage_options()
        df.to_parquet(key, index=False, storage_options=storage_options)
        _write_notice_s3(key, sources, storage_options)
    else:
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        (path.parent / "NOTICE.txt").write_text(attribution_block(sources) + "\n")
    logger.info("wrote {} rows -> {}", len(df), key)
    return key


def _write_notice_s3(key: str, sources: list[Source], storage_options: dict) -> None:
    import s3fs

    fs = s3fs.S3FileSystem(
        key=storage_options["key"],
        secret=storage_options["secret"],
        client_kwargs=storage_options["client_kwargs"],
    )
    notice_key = key.rsplit("/", 1)[0] + "/NOTICE.txt"
    with fs.open(notice_key, "w") as fh:
        fh.write(attribution_block(sources) + "\n")
