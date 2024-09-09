"""The NLP work itself.

Nothing in this module imports Flask, boto3 or ``os.environ``. It is handed a
callable that returns a loaded spaCy ``Language`` and it does linguistics with it,
which is what makes it testable against ``spacy.blank("en")`` in milliseconds and
with no AWS credentials in the environment.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Callable

from nlp_lambda.errors import ValidationError

logger = logging.getLogger(__name__)

LOCATION_LABELS = ("GPE", "LOC")


@dataclass(frozen=True)
class Entity:
    """One named entity occurrence, with the offsets needed to highlight it."""

    text: str
    label: str
    start_char: int
    end_char: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Analyzer:
    """A named way of reducing a spaCy ``Doc`` to JSON.

    ``requires`` lists components the analyzer cannot work without — a model that
    lacks one is rejected with a clear message. ``keeps`` lists components that
    are not the point of the analyzer but that the required ones depend on.
    Everything else is disabled for the run, which is where most of the
    per-request time goes: the dependency parser is the most expensive component
    in a spaCy pipeline and an entity extractor has no use for it.

    The split matters. ``doc.noun_chunks`` needs the parser *and* the POS tags the
    tagger and attribute_ruler produce; run it with the parser alone and it
    silently returns an empty list rather than raising. The ``en_core_web_*`` NER
    component, by contrast, carries its own tok2vec, so ``entities`` needs nothing
    upstream (verified against en_core_web_sm and en_core_web_md 3.8.0: identical
    output with every other component disabled).
    """

    name: str
    run: Callable[[object], object]
    requires: tuple[str, ...] = ()
    description: str = ""
    keeps: tuple[str, ...] = ()


_ANALYZERS: dict[str, Analyzer] = {}


def register_analyzer(analyzer: Analyzer) -> None:
    """Add or replace an analyzer. Keeps new outputs out of the HTTP layer."""

    _ANALYZERS[analyzer.name] = analyzer


def available_analyzers() -> tuple[str, ...]:
    return tuple(sorted(_ANALYZERS))


def describe_analyzer(name: str) -> str:
    return _ANALYZERS[name].description


def _entities(doc) -> list[dict]:
    return [
        Entity(ent.text, ent.label_, ent.start_char, ent.end_char).as_dict() for ent in doc.ents
    ]


def _locations(doc) -> list[str]:
    """Location mentions, most frequent first.

    The original implementation returned ``list(Counter(...).keys())`` under the
    name ``locations_sorted_by_num_appearances``. ``Counter`` is a ``dict``, so
    that is *insertion* order — the ranking the name promised never happened.
    """

    names = [ent.text for ent in doc.ents if ent.label_ in LOCATION_LABELS]
    return [name for name, _ in Counter(names).most_common()]


def _noun_chunks(doc) -> list[str]:
    return [chunk.text for chunk in doc.noun_chunks]


register_analyzer(Analyzer("entities", _entities, ("ner",), "Every named entity with its offsets"))
register_analyzer(
    Analyzer("locations", _locations, ("ner",), "GPE/LOC mentions, most frequent first")
)
register_analyzer(
    Analyzer(
        "noun_chunks",
        _noun_chunks,
        ("parser",),
        "Base noun phrases",
        keeps=("tok2vec", "tagger", "attribute_ruler"),
    )
)


@dataclass
class ServiceStats:
    """Counters a ``/health`` call can report without loading the model."""

    model_loaded: bool = False
    load_seconds: float | None = None
    documents_processed: int = 0
    extra: dict = field(default_factory=dict)


class NlpService:
    """Runs analyzers over text using a lazily loaded spaCy pipeline."""

    def __init__(
        self,
        loader: Callable[[], object],
        *,
        max_text_chars: int = 100_000,
        max_batch_size: int = 32,
    ) -> None:
        self._loader = loader
        self._nlp = None
        self.max_text_chars = max_text_chars
        self.max_batch_size = max_batch_size
        self.stats = ServiceStats()

    # -- lifecycle -------------------------------------------------------------

    @property
    def nlp(self):
        """The loaded pipeline. Loading happens on first use, never on import."""

        if self._nlp is None:
            started = time.perf_counter()
            self._nlp = self._loader()
            elapsed = time.perf_counter() - started
            self.stats.model_loaded = True
            self.stats.load_seconds = elapsed
            logger.info(
                "pipeline_loaded name=%s seconds=%.3f components=%s",
                getattr(self._nlp, "meta", {}).get("name", "unknown"),
                elapsed,
                ",".join(self._nlp.pipe_names),
            )
        return self._nlp

    @property
    def is_loaded(self) -> bool:
        return self._nlp is not None

    def warm_up(self) -> dict:
        """Force the model load and report what it cost.

        Called by the ``/warmup`` route and by a scheduled ping, so the first real
        user request does not pay for the download.
        """

        was_loaded = self.is_loaded
        nlp = self.nlp
        return {
            "already_warm": was_loaded,
            "pipeline": getattr(nlp, "meta", {}).get("name"),
            "components": list(nlp.pipe_names),
            "load_seconds": self.stats.load_seconds,
        }

    # -- work ------------------------------------------------------------------

    def _validate(self, texts: Sequence[str]) -> list[str]:
        if not texts:
            raise ValidationError("at least one text is required")
        if len(texts) > self.max_batch_size:
            raise ValidationError(
                f"batch of {len(texts)} exceeds max_batch_size={self.max_batch_size}"
            )
        cleaned: list[str] = []
        for index, text in enumerate(texts):
            if not isinstance(text, str):
                raise ValidationError(f"text at index {index} is not a string")
            if len(text) > self.max_text_chars:
                raise ValidationError(
                    f"text at index {index} is {len(text)} characters, "
                    f"over the {self.max_text_chars} limit"
                )
            cleaned.append(text)
        return cleaned

    def _disabled_for(self, analyzer: Analyzer) -> list[str]:
        keep = set(analyzer.requires) | set(analyzer.keeps)
        return [name for name in self.nlp.pipe_names if name not in keep]

    def analyze(self, texts: Sequence[str], analyzer: str = "entities") -> list[object]:
        """Run one analyzer over a batch of texts and return one result per text."""

        try:
            chosen = _ANALYZERS[analyzer]
        except KeyError as exc:
            raise ValidationError(
                f"unknown analyzer {analyzer!r}; available: {', '.join(available_analyzers())}"
            ) from exc

        cleaned = self._validate(texts)
        disable = self._disabled_for(chosen)
        missing = [name for name in chosen.requires if name not in self.nlp.pipe_names]
        if missing:
            raise ValidationError(
                f"analyzer {analyzer!r} needs pipeline component(s) "
                f"{', '.join(missing)}, which this model does not have"
            )

        started = time.perf_counter()
        docs: Iterable = self.nlp.pipe(cleaned, batch_size=self.max_batch_size, disable=disable)
        results = [chosen.run(doc) for doc in docs]
        self.stats.documents_processed += len(cleaned)
        logger.info(
            "analyze analyzer=%s documents=%d chars=%d seconds=%.4f disabled=%s",
            analyzer,
            len(cleaned),
            sum(len(t) for t in cleaned),
            time.perf_counter() - started,
            ",".join(disable) or "-",
        )
        return results

    def find_locations(self, text: str) -> list[str]:
        """Backwards-compatible shorthand for the original single-text endpoint."""

        return self.analyze([text], analyzer="locations")[0]
