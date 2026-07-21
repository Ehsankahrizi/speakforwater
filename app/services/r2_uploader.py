"""
Upload media (episode MP3s, intro audio, hero video) to Cloudflare R2.

R2 exposes an S3-compatible API, so we use boto3. Everything here is a no-op
unless the R2_* environment variables are set, which keeps the pipeline working
unchanged until the R2 cutover is done.

Required environment variables (set as GitHub Actions secrets):
  R2_ACCOUNT_ID         — Cloudflare account id (the R2 endpoint host)
  R2_ACCESS_KEY_ID      — R2 API token access key
  R2_SECRET_ACCESS_KEY  — R2 API token secret
  R2_BUCKET             — bucket name (e.g. speakforwater-media)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def r2_enabled() -> bool:
    """True only when every R2 credential is present."""
    return all(
        os.getenv(k)
        for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    )


def _client():
    import boto3  # imported lazily so the dependency is only needed when R2 is on
    from botocore.config import Config

    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
}


def object_exists(key: str) -> bool:
    """True if an object with `key` exists in the bucket."""
    if not r2_enabled():
        return False
    try:
        _client().head_object(Bucket=os.environ["R2_BUCKET"], Key=key)
        return True
    except Exception:
        return False


def download_file(key: str, local_path: Path) -> bool:
    """Download R2 object `key` to `local_path`. Returns True on success."""
    if not r2_enabled():
        return False
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _client().download_file(os.environ["R2_BUCKET"], key, str(local_path))
        logger.info(f"[r2] downloaded {key} -> {local_path}")
        return True
    except Exception as e:
        logger.error(f"[r2] download failed for {key}: {e}")
        return False


def copy_object(src_key: str, dst_key: str) -> bool:
    """Server-side copy `src_key` to `dst_key` within the bucket."""
    if not r2_enabled():
        return False
    bucket = os.environ["R2_BUCKET"]
    try:
        _client().copy_object(
            Bucket=bucket,
            Key=dst_key,
            CopySource={"Bucket": bucket, "Key": src_key},
        )
        logger.info(f"[r2] copied {src_key} -> {dst_key}")
        return True
    except Exception as e:
        logger.error(f"[r2] copy failed {src_key} -> {dst_key}: {e}")
        return False


def upload_file(local_path: Path, key: str) -> bool:
    """Upload a local file to R2 under `key` (e.g. "episodes/ep082.mp3").

    Returns True on success, False if R2 isn't configured or the upload failed.
    Audio/video are served publicly with a long cache lifetime since the
    filenames are immutable.
    """
    if not r2_enabled():
        return False
    local_path = Path(local_path)
    if not local_path.exists():
        logger.warning(f"[r2] file not found, skipping: {local_path}")
        return False
    ctype = _CONTENT_TYPES.get(local_path.suffix.lower(), "application/octet-stream")
    try:
        _client().upload_file(
            str(local_path),
            os.environ["R2_BUCKET"],
            key,
            ExtraArgs={
                "ContentType": ctype,
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )
        logger.info(f"[r2] uploaded {local_path.name} -> {key}")
        return True
    except Exception as e:
        logger.error(f"[r2] upload failed for {key}: {e}")
        return False
