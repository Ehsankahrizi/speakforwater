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
