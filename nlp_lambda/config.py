"""Settings, read once from the environment.

Every value has a default or is ``None``; nothing here raises at import time. The
original code did ``os.environ['SPACY_MODEL_MEDIUM']`` at module scope, so importing
the app on a box without a ``.env`` file died with a ``KeyError`` before Flask ever
started. A Lambda that cannot import cannot tell you *why* it cannot import.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from nlp_lambda.errors import ConfigError

#: Lambda gives a function 512 MB of ``/tmp`` unless ephemeral storage is raised.
LAMBDA_TMP_BYTES = 512 * 1024 * 1024

DEFAULT_MODEL = "en_core_web_sm-3.8.0"
DEFAULT_CACHE_DIR = Path("/tmp/models")
DEFAULT_S3_PREFIX = "models"


def load_dotenv_if_available(path: str | None = None) -> bool:
    """Load a ``.env`` file when python-dotenv is installed.

    Called from the entrypoints, never from library code, so importing this
    package never has a side effect on the process environment.
    """

    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    return bool(load_dotenv(path) if path else load_dotenv())


def _as_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Immutable view of the process environment.

    Built once per container and shared by every warm invocation.
    """

    model_name: str = DEFAULT_MODEL
    model_source: str = "s3"
    cache_dir: Path = DEFAULT_CACHE_DIR
    s3_bucket: str | None = None
    s3_prefix: str = DEFAULT_S3_PREFIX
    s3_endpoint_url: str | None = None
    local_model_root: Path | None = None
    #: Refuse to start a download that cannot fit, leaving this much headroom.
    cache_reserve_bytes: int = 64 * 1024 * 1024
    max_text_chars: int = 100_000
    max_batch_size: int = 32
    sentry_dsn: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        local_root = env.get("LOCAL_MODEL_ROOT")
        return cls(
            # SPACY_MODEL_MEDIUM is the name the original .env used; keep reading it
            # so an existing deployment does not silently switch models on upgrade.
            model_name=env.get("SPACY_MODEL") or env.get("SPACY_MODEL_MEDIUM") or DEFAULT_MODEL,
            model_source=(env.get("MODEL_SOURCE") or "s3").strip().lower(),
            cache_dir=Path(env.get("MODEL_CACHE_DIR") or DEFAULT_CACHE_DIR),
            s3_bucket=env.get("S3_BUCKET") or None,
            s3_prefix=(env.get("S3_PREFIX") or DEFAULT_S3_PREFIX).strip("/"),
            s3_endpoint_url=env.get("S3_ENDPOINT_URL") or None,
            local_model_root=Path(local_root) if local_root else None,
            cache_reserve_bytes=_as_int(env, "CACHE_RESERVE_BYTES", cls.cache_reserve_bytes),
            max_text_chars=_as_int(env, "MAX_TEXT_CHARS", cls.max_text_chars),
            max_batch_size=_as_int(env, "MAX_BATCH_SIZE", cls.max_batch_size),
            sentry_dsn=env.get("SENTRY_DSN") or None,
        )

    def require_s3_bucket(self) -> str:
        if not self.s3_bucket:
            raise ConfigError("S3_BUCKET is not set; it is required when MODEL_SOURCE=s3")
        return self.s3_bucket

    def require_local_model_root(self) -> Path:
        if self.local_model_root is None:
            raise ConfigError("LOCAL_MODEL_ROOT is not set; it is required when MODEL_SOURCE=local")
        return self.local_model_root
