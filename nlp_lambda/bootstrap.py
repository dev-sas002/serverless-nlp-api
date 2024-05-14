"""Composition root: turn ``Settings`` into a ready :class:`NlpService`.

This is the only module that knows about all the layers at once. Keeping the wiring
here is what lets ``service.py`` stay free of AWS and ``model_source.py`` stay free
of spaCy — dependencies point inward, and the arrows all meet in this file.
"""

from __future__ import annotations

import logging

from nlp_lambda.config import Settings
from nlp_lambda.model_cache import ModelCache
from nlp_lambda.model_source import build_model_source
from nlp_lambda.service import NlpService

logger = logging.getLogger(__name__)


def build_model_cache(settings: Settings) -> ModelCache:
    return ModelCache(
        build_model_source(settings),
        settings.cache_dir,
        reserve_bytes=settings.cache_reserve_bytes,
    )


def build_service(settings: Settings, cache: ModelCache | None = None) -> NlpService:
    """Build the service with a loader that fetches on first use, not on import."""

    cache = cache or build_model_cache(settings)

    def load():
        import spacy  # kept out of module scope so `--help` and /health stay fast

        result = cache.ensure(settings.model_name)
        logger.info(
            "model_ready model=%s cache_hit=%s seconds=%.3f path=%s",
            settings.model_name,
            result.cache_hit,
            result.seconds,
            result.path,
        )
        return spacy.load(result.path)

    service = NlpService(
        load,
        max_text_chars=settings.max_text_chars,
        max_batch_size=settings.max_batch_size,
    )
    service.stats.extra["model_name"] = settings.model_name
    service.stats.extra["model_source"] = settings.model_source
    return service
