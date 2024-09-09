"""The Lambda cold-start path, end to end, with S3 served by moto.

Every other test injects a ready-made service. These build the app the way
``app.py`` does on Lambda — settings only — so the first request has to fetch the
archive from the bucket, unpack it into the cache directory and ``spacy.load`` it.
The pipeline in the archive is a real, serialised spaCy pipeline (blank English
plus an entity ruler named ``ner``), so nothing is downloaded from the internet.
"""

from __future__ import annotations

import tarfile

import boto3
import pytest
from moto import mock_aws

from nlp_lambda.api import create_app
from nlp_lambda.config import Settings

BUCKET = "cold-start-models"
MODEL = "en_ruler_model-1.0.0"


@pytest.fixture
def seeded_bucket(tmp_path):
    import spacy

    nlp = spacy.blank("en")
    nlp.add_pipe("entity_ruler", name="ner").add_patterns(
        [{"label": "GPE", "pattern": "Lisbon"}, {"label": "PERSON", "pattern": "Ada Lovelace"}]
    )
    outer = tmp_path / "build" / MODEL
    pipeline_dir = outer / "en_ruler_model" / MODEL
    pipeline_dir.mkdir(parents=True)
    nlp.to_disk(pipeline_dir)
    archive = tmp_path / f"{MODEL}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(outer, arcname=MODEL)

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.upload_file(str(archive), BUCKET, f"models/{MODEL}.tar.gz")
        yield s3


def _settings(cache_dir) -> Settings:
    return Settings.from_env(
        {
            "SPACY_MODEL": MODEL,
            "MODEL_SOURCE": "s3",
            "S3_BUCKET": BUCKET,
            "MODEL_CACHE_DIR": str(cache_dir),
        }
    )


def test_first_request_fetches_from_s3_and_answers(seeded_bucket, tmp_path):
    cache_dir = tmp_path / "lambda-tmp"
    client = create_app(_settings(cache_dir)).test_client()

    assert client.get("/health").get_json()["model_loaded"] is False
    assert not cache_dir.exists()

    response = client.post("/analyze", json={"text": "Ada Lovelace flew to Lisbon."})

    assert response.status_code == 200
    assert [(e["text"], e["label"]) for e in response.get_json()["results"][0]] == [
        ("Ada Lovelace", "PERSON"),
        ("Lisbon", "GPE"),
    ]
    assert (cache_dir / MODEL).is_dir()
    assert not list(cache_dir.glob("*.tar.gz")), "the archive should be deleted once unpacked"


def test_a_new_process_on_a_warm_container_skips_s3(seeded_bucket, tmp_path):
    cache_dir = tmp_path / "lambda-tmp"
    create_app(_settings(cache_dir)).test_client().post("/warmup")
    seeded_bucket.delete_object(Bucket=BUCKET, Key=f"models/{MODEL}.tar.gz")

    fresh_process = create_app(_settings(cache_dir)).test_client()
    response = fresh_process.post("/find_locations", json={"text": "Lisbon, then Lisbon again"})

    assert response.status_code == 200
    assert response.get_json() == ["Lisbon"]


def test_a_missing_object_is_a_503_naming_the_key(seeded_bucket, tmp_path):
    settings = Settings.from_env(
        {"SPACY_MODEL": "en_absent-0.0.1", "S3_BUCKET": BUCKET, "MODEL_CACHE_DIR": str(tmp_path)}
    )

    response = create_app(settings).test_client().post("/analyze", json={"text": "hi"})

    assert response.status_code == 503
    assert "models/en_absent-0.0.1.tar.gz" in response.get_json()["detail"]
