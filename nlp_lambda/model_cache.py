"""The ``/tmp`` cache that turns a cold start into a one-off cost.

A Lambda container is reused for many invocations. The model download must happen
on the first one and never again, so:

* the unpacked model stays in ``/tmp`` (the only writable path on Lambda), which
  survives for the life of the container;
* a process-level memo means warm invocations do not even ``stat`` the disk;
* the download is staged in a sibling directory and renamed into place, so a
  container killed mid-download cannot leave a half-model that the next
  invocation would happily try to load.

``/tmp`` is 512 MB by default. Checking the fetch against the free space before
starting it turns "``OSError: [Errno 28] No space left on device``, somewhere inside
tarfile" into a single actionable log line.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from nlp_lambda.errors import InsufficientSpaceError
from nlp_lambda.model_source import ModelSource

logger = logging.getLogger(__name__)

#: Assumed unpacked size, as a multiple of the gzipped archive. Measured on this
#: project: en_core_web_sm 1.19x, en_core_web_md 1.69x. 2.5x is deliberately
#: pessimistic, and the pre-flight budget is archive + unpacked (so 3.5x) because
#: both exist on disk at the same moment during extraction.
ARCHIVE_EXPANSION_FACTOR = 2.5


@dataclass(frozen=True)
class FetchResult:
    """What happened when the cache was asked for a model."""

    path: Path
    cache_hit: bool
    seconds: float
    bytes_on_disk: int


def directory_size(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


class ModelCache:
    """Resolve a model name to a directory on disk, fetching it at most once."""

    def __init__(
        self,
        source: ModelSource,
        cache_dir: Path,
        *,
        reserve_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.source = source
        self.cache_dir = Path(cache_dir)
        self.reserve_bytes = reserve_bytes
        self._memo: dict[str, Path] = {}
        self._lock = threading.Lock()

    # -- space -----------------------------------------------------------------

    def free_bytes(self) -> int:
        probe = self.cache_dir
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return shutil.disk_usage(probe).free

    def check_space(self, model_name: str) -> None:
        compressed = self.source.size_bytes(model_name)
        if not compressed:
            return
        needed = int(compressed * (1 + ARCHIVE_EXPANSION_FACTOR)) + self.reserve_bytes
        free = self.free_bytes()
        if free < needed:
            raise InsufficientSpaceError(
                f"{model_name} needs about {needed / 1e6:.0f} MB "
                f"(archive {compressed / 1e6:.0f} MB + unpacked + "
                f"{self.reserve_bytes / 1e6:.0f} MB reserve) but only "
                f"{free / 1e6:.0f} MB is free in {self.cache_dir}. On Lambda, raise "
                "ephemeral storage above the 512 MB default or use a smaller model."
            )

    # -- fetch -----------------------------------------------------------------

    def ensure(self, model_name: str) -> FetchResult:
        started = time.perf_counter()
        cached = self._memo.get(model_name)
        if cached is not None and cached.exists():
            return FetchResult(cached, True, time.perf_counter() - started, 0)

        with self._lock:
            cached = self._memo.get(model_name)
            if cached is not None and cached.exists():
                return FetchResult(cached, True, time.perf_counter() - started, 0)

            final = self.cache_dir / model_name
            if final.exists():
                # Same container, different process (or a warm start after an import
                # reload): the bytes are there, only the memo is cold.
                from nlp_lambda.archive import find_model_dir

                path = find_model_dir(final)
                self._memo[model_name] = path
                elapsed = time.perf_counter() - started
                logger.info("model_cache disk_hit model=%s seconds=%.3f", model_name, elapsed)
                return FetchResult(path, True, elapsed, directory_size(final))

            self.check_space(model_name)
            staging = self.cache_dir / f".staging-{model_name}"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            try:
                fetched = Path(self.source.fetch(model_name, staging))
                path = self._promote(fetched, staging, final)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

            self._memo[model_name] = path
            elapsed = time.perf_counter() - started
            size = directory_size(final) if final.exists() else directory_size(path)
            logger.info(
                "model_cache miss model=%s source=%s seconds=%.3f bytes=%d",
                model_name,
                getattr(self.source, "name", type(self.source).__name__),
                elapsed,
                size,
            )
            return FetchResult(path, False, elapsed, size)

    def _promote(self, fetched: Path, staging: Path, final: Path) -> Path:
        """Move a staged download into its final name atomically."""

        try:
            relative = fetched.relative_to(staging)
        except ValueError:
            # The source did not copy anything (LocalModelSource); use it in place.
            shutil.rmtree(staging, ignore_errors=True)
            return fetched
        staging.rename(final)
        return final / relative

    # -- introspection ---------------------------------------------------------

    def stats(self) -> dict:
        return {
            "cache_dir": str(self.cache_dir),
            "source": getattr(self.source, "name", type(self.source).__name__),
            "cached_models": sorted(self._memo),
            "free_bytes": self.free_bytes(),
        }
