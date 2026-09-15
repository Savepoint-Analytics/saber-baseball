"""Mirror the local bronze layer into MinIO on Savepoint-Lab.

Bronze is meant to live in object storage (``CLAUDE.md`` §4) but ingestion writes
locally whenever ``SABER_STORAGE_BACKEND=local``. This walks the local tree and
uploads what is missing, so a laptop-run backfill can be promoted to the lab without
re-pulling anything from upstream — which matters because a full Statcast backfill is
hours of rate-limited requests to Savant that we should never repeat unnecessarily.

Everything lands under ``MINIO_PREFIX`` (e.g. ``s3://sabermetrics/baseball/...``).
The bucket is shared with the other verticals, so writing to the root would collide
with them — the object-store analogue of the schema-per-vertical rule for Postgres.

Idempotent: an object already present at the same byte size is skipped, so this is
safe to re-run and safe to interrupt.

Run::

    python -m src.warehouse.sync_minio --dry-run
    python -m src.warehouse.sync_minio --dataset lahman --dataset statsapi
"""

from __future__ import annotations

from pathlib import Path

import boto3
import typer
from loguru import logger

from src.config import settings

app = typer.Typer(add_completion=False, help="Mirror local bronze into MinIO.")


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
    )


def local_bronze_root() -> Path:
    """The on-disk bronze tree, regardless of the configured backend."""
    return (settings.data_dir / "bronze").resolve()


def object_key(path: Path, root: Path) -> str:
    """S3 key for a local bronze file, under the vertical's prefix."""
    rel = path.relative_to(root).as_posix()
    prefix = settings.minio_prefix.strip("/")
    return f"{prefix}/{rel}" if prefix else rel


def existing_sizes(client, bucket: str, prefix: str) -> dict[str, int]:
    """One paginated LIST instead of a HEAD per file — far fewer round trips."""
    sizes: dict[str, int] = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            sizes[obj["Key"]] = obj["Size"]
    return sizes


def sync(datasets: list[str] | None = None, dry_run: bool = False) -> dict[str, int]:
    """Upload every local bronze file missing from MinIO. Returns per-dataset counts."""
    root = local_bronze_root()
    if not root.exists():
        raise FileNotFoundError(f"No local bronze tree at {root}")

    client = _client()
    bucket = settings.minio_bucket
    prefix = settings.minio_prefix.strip("/")
    remote = existing_sizes(client, bucket, prefix)
    logger.info("MinIO: {} object(s) already under s3://{}/{}", len(remote), bucket, prefix)

    uploaded: dict[str, int] = {}
    skipped = 0
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        dataset = path.relative_to(root).parts[0]
        if datasets and dataset not in datasets:
            continue

        key = object_key(path, root)
        size = path.stat().st_size
        if remote.get(key) == size:
            skipped += 1
            continue
        if not dry_run:
            client.upload_file(str(path), bucket, key)
        uploaded[dataset] = uploaded.get(dataset, 0) + 1
        total_bytes += size
        if sum(uploaded.values()) % 200 == 0:
            logger.info("… {} uploaded ({:.0f} MB)", sum(uploaded.values()), total_bytes / 1e6)

    verb = "would upload" if dry_run else "uploaded"
    logger.success(
        "{} {} file(s) ({:.1f} MB); {} already present",
        verb,
        sum(uploaded.values()),
        total_bytes / 1e6,
        skipped,
    )
    return uploaded


@app.command()
def main(
    dataset: list[str] = typer.Option(None, "--dataset", help="Limit to these bronze datasets."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without uploading."),
) -> None:
    counts = sync(datasets=list(dataset) if dataset else None, dry_run=dry_run)
    for name, n in sorted(counts.items()):
        logger.info("  {}: {} file(s)", name, n)


if __name__ == "__main__":
    app()
