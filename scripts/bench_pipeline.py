"""Measure what disabling the unused spaCy components buys, on this machine.

    MODEL_SOURCE=local LOCAL_MODEL_ROOT=/tmp/models \
        python -m scripts.bench_pipeline

Run it before believing the numbers in the README: they were measured on a laptop,
and Lambda's CPU allocation scales with the memory you configure.
"""

from __future__ import annotations

import statistics
import time

from nlp_lambda.bootstrap import build_service
from nlp_lambda.config import Settings, load_dotenv_if_available

PARAGRAPH = (
    "Reuters reported from Madrid that Banco Santander will open an office in "
    "Lisbon next year, while its rivals in Frankfurt and the UK wait for the "
    "European Central Bank to move. Analysts in New York were sceptical. "
)
ROUNDS = 5


def bench(fn, rounds: int = ROUNDS) -> tuple[float, float]:
    times = []
    for _ in range(rounds):
        started = time.perf_counter()
        fn()
        times.append(time.perf_counter() - started)
    return min(times), statistics.median(times)


def main() -> int:
    load_dotenv_if_available()
    service = build_service(Settings.from_env())
    service.warm_up()

    texts = [PARAGRAPH * 3] * service.max_batch_size
    chars = sum(len(text) for text in texts)

    full_min, full_med = bench(lambda: list(service.nlp.pipe(texts, batch_size=len(texts))))
    ner_min, ner_med = bench(lambda: service.analyze(texts, analyzer="entities"))

    print(f"batch of {len(texts)} documents, {chars} characters total, {ROUNDS} rounds")
    print(
        f"  all {len(service.nlp.pipe_names)} components enabled          "
        f": median {full_med * 1000:7.1f} ms   best {full_min * 1000:7.1f} ms"
    )
    print(
        f"  entities analyzer (ner only)      "
        f": median {ner_med * 1000:7.1f} ms   best {ner_min * 1000:7.1f} ms"
    )
    print(f"  speedup (median)                  : {full_med / ner_med:.2f}x")
    print(f"  per document, entities analyzer   : {ner_med / len(texts) * 1000:.2f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
