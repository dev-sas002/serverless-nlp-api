"""Operator commands: seed the bucket, prime the cache, look at what is there.

``python -m nlp_lambda.cli --help``

These are the jobs that used to live in ``if __name__ == "__main__"`` at the bottom
of the model-download module, with the interesting variants commented out.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from nlp_lambda.bootstrap import build_model_cache, build_service
from nlp_lambda.config import Settings, load_dotenv_if_available
from nlp_lambda.errors import NlpLambdaError
from nlp_lambda.model_source import GitHubModelSource, S3ModelSource

logger = logging.getLogger(__name__)


def cmd_download(args, settings: Settings) -> int:
    """Download a model archive from the spaCy releases page."""

    dest = Path(args.dest or settings.cache_dir)
    archive = GitHubModelSource().download_archive(settings.model_name, dest)
    print(f"{archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    return 0


def cmd_upload(args, settings: Settings) -> int:
    """Put a model archive in S3 so Lambda can fetch it at runtime."""

    dest = Path(args.dest or settings.cache_dir)
    archive = dest / f"{settings.model_name}.tar.gz"
    if not archive.exists():
        print(f"{archive} not on disk, fetching from spaCy releases")
        archive = GitHubModelSource().download_archive(settings.model_name, dest)

    s3 = S3ModelSource(
        settings.require_s3_bucket(),
        settings.s3_prefix,
        endpoint_url=settings.s3_endpoint_url,
    )
    key = s3.key_for(settings.model_name)
    s3.client.upload_file(str(archive), s3.bucket, key)
    print(f"uploaded s3://{s3.bucket}/{key} ({archive.stat().st_size / 1e6:.1f} MB)")
    return 0


def cmd_warm(_args, settings: Settings) -> int:
    """Fetch and load the model exactly as a cold Lambda invocation would."""

    started = time.perf_counter()
    cache = build_model_cache(settings)
    fetch = cache.ensure(settings.model_name)
    fetched_at = time.perf_counter()

    import spacy

    nlp = spacy.load(fetch.path)
    loaded_at = time.perf_counter()
    doc = nlp("Warm up in Madrid.")
    print(
        json.dumps(
            {
                "model": settings.model_name,
                "source": settings.model_source,
                "cache_hit": fetch.cache_hit,
                "fetch_seconds": round(fetched_at - started, 3),
                "spacy_load_seconds": round(loaded_at - fetched_at, 3),
                "first_doc_seconds": round(time.perf_counter() - loaded_at, 3),
                "bytes_on_disk": fetch.bytes_on_disk,
                "components": list(nlp.pipe_names),
                "entities": [(e.text, e.label_) for e in doc.ents],
            },
            indent=2,
        )
    )
    return 0


def cmd_analyze(args, settings: Settings) -> int:
    """Run an analyzer over text from the command line."""

    service = build_service(settings)
    text = args.text if args.text is not None else sys.stdin.read()
    print(json.dumps(service.analyze([text], analyzer=args.analyzer)[0], indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nlp_lambda.cli", description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="model name, e.g. en_core_web_sm-3.7.1")
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser("download", help=cmd_download.__doc__)
    download.add_argument("--dest")
    download.set_defaults(func=cmd_download)

    upload = sub.add_parser("upload", help=cmd_upload.__doc__)
    upload.add_argument("--dest")
    upload.set_defaults(func=cmd_upload)

    warm = sub.add_parser("warm", help=cmd_warm.__doc__)
    warm.set_defaults(func=cmd_warm)

    analyze = sub.add_parser("analyze", help=cmd_analyze.__doc__)
    analyze.add_argument("text", nargs="?", help="text to analyze; omit to read stdin")
    analyze.add_argument("--analyzer", default="entities")
    analyze.set_defaults(func=cmd_analyze)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    load_dotenv_if_available()
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    if args.model:
        settings = Settings(**{**settings.__dict__, "model_name": args.model})
    try:
        return args.func(args, settings)
    except NlpLambdaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
