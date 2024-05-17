"""Shared fixtures.

Two invariants the whole suite depends on, enforced here rather than trusted:

1. **No test reaches AWS.** Real credentials are scrubbed from the environment and
   replaced with obvious fakes, so a test that accidentally calls S3 for real
   fails loudly instead of billing someone.
2. **No test downloads a model.** The spaCy pipelines used here are
   ``spacy.blank("en")`` with an ``entity_ruler`` standing in for the NER
   component, and the "model archives" are a handful of text files in a tarball.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

AWS_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SECURITY_TOKEN",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
)

PROJECT_ENV_VARS = (
    "SPACY_MODEL",
    "SPACY_MODEL_MEDIUM",
    "SPACY_MODEL_SMALL",
    "MODEL_SOURCE",
    "MODEL_CACHE_DIR",
    "S3_BUCKET",
    "S3_PREFIX",
    "S3_ENDPOINT_URL",
    "LOCAL_MODEL_ROOT",
    "SENTRY_DSN",
)

MODEL_NAME = "en_test_model-1.0.0"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Scrub real credentials and project settings out of every test."""

    for name in PROJECT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in AWS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")


def build_model_tree(root: Path, model_name: str = MODEL_NAME) -> Path:
    """Create the directory layout a real spaCy model archive unpacks to.

    ``<name>-<version>/<name>/<name>-<version>/`` — an sdist wrapper around the
    directory ``spacy.load`` actually wants.
    """

    package = model_name.split("-")[0]
    outer = Path(root) / model_name
    inner = outer / package / model_name
    inner.mkdir(parents=True, exist_ok=True)
    (inner / "config.cfg").write_text('[nlp]\nlang = "en"\npipeline = []\n')
    (inner / "meta.json").write_text('{"lang": "en", "name": "test_model"}')
    (outer / "meta.json").write_text('{"lang": "en", "name": "test_model"}')
    (outer / "setup.py").write_text("# sdist wrapper\n")
    return outer


def build_model_archive(tmp_path: Path, model_name: str = MODEL_NAME) -> Path:
    """Tar up a fake model tree the way spaCy publishes them."""

    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    tree = build_model_tree(staging, model_name)
    archive = tmp_path / f"{model_name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(tree, arcname=model_name)
    return archive


@pytest.fixture
def model_archive(tmp_path):
    return build_model_archive(tmp_path / "archives")


@pytest.fixture
def local_model_root(tmp_path):
    root = tmp_path / "local_models"
    build_model_tree(root)
    return root


@pytest.fixture
def blank_nlp():
    """A spaCy pipeline with a rule-based stand-in for the NER component.

    The component is *named* ``ner`` so analyzers that declare ``requires=("ner",)``
    are satisfied, but it is an ``entity_ruler``: deterministic, instant, and
    nothing is downloaded.
    """

    import spacy

    nlp = spacy.blank("en")
    ruler = nlp.add_pipe("entity_ruler", name="ner")
    ruler.add_patterns(
        [
            {"label": "GPE", "pattern": "Spain"},
            {"label": "GPE", "pattern": "the UK"},
            {"label": "GPE", "pattern": "Madrid"},
            {"label": "PERSON", "pattern": "Ada Lovelace"},
        ]
    )
    return nlp
