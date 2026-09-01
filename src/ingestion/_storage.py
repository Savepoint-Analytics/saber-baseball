"""Bronze-layer write helpers: land raw pulls as dated Parquet (local or MinIO).

Keys are date-partitioned per ``CLAUDE.md`` §4, e.g.::

    bronze/statcast/season=2025/date=2025-06-24.parquet

A NOTICE.txt carrying the source attribution is written alongside each dataset so
the required notice always travels with the raw landing zone.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import duckdb
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


def bronze_duckdb() -> duckdb.DuckDBPyConnection:
    """A DuckDB connection able to read the bronze layer on the current backend.

    Reading bronze is not simply ``read_parquet`` once the backend is MinIO: DuckDB
    needs httpfs loaded and an S3 secret, or the same query that works locally comes
    back as an opaque 403. Anything that reads bronze should go through here rather
    than calling ``duckdb.sql`` directly, so switching SABER_STORAGE_BACKEND stays a
    one-line change instead of breaking callers one at a time.
    """
    con = duckdb.connect()
    if settings.storage_backend != "minio":
        return con
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(
        """
        CREATE OR REPLACE SECRET bronze_s3 (
            TYPE S3, KEY_ID $key, SECRET $secret,
            ENDPOINT $endpoint, URL_STYLE 'path', USE_SSL $use_ssl
        )
        """,
        {
            "key": settings.minio_access_key,
            "secret": settings.minio_secret_key,
            "endpoint": urlparse(settings.minio_endpoint).netloc,
            "use_ssl": settings.minio_endpoint.startswith("https"),
        },
    )
    return con
