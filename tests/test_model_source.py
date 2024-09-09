import pytest
from moto import mock_aws

from nlp_lambda.config import Settings
from nlp_lambda.errors import ConfigError, ModelUnavailableError
from nlp_lambda.model_source import (
    GitHubModelSource,
    LocalModelSource,
    ModelSource,
    S3ModelSource,
    available_model_sources,
    build_model_source,
    register_model_source,
)
from tests.conftest import MODEL_NAME


@pytest.fixture
def s3_bucket(model_archive):
    """A moto-backed bucket holding one fake model archive. No network, no AWS."""

    import boto3

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-models")
        client.upload_file(str(model_archive), "test-models", f"models/{MODEL_NAME}.tar.gz")
        yield client


def test_s3_source_downloads_unpacks_and_resolves(s3_bucket, tmp_path):
    source = S3ModelSource("test-models", "models", client=s3_bucket)

    path = source.fetch(MODEL_NAME, tmp_path / "cache")

    assert (path / "config.cfg").is_file()
    # The tarball is removed after unpacking; /tmp is only 512 MB on Lambda.
    assert not (tmp_path / "cache" / f"{MODEL_NAME}.tar.gz").exists()


def test_s3_source_reports_the_archive_size_without_downloading(s3_bucket):
    source = S3ModelSource("test-models", "models", client=s3_bucket)

    assert source.size_bytes(MODEL_NAME) > 0
    assert source.size_bytes("no_such_model-1.0.0") is None


def test_missing_object_becomes_a_model_error_with_the_key_in_it(s3_bucket, tmp_path):
    source = S3ModelSource("test-models", "models", client=s3_bucket)

    with pytest.raises(ModelUnavailableError, match="models/absent-1.0.0.tar.gz"):
        source.fetch("absent-1.0.0", tmp_path / "cache")


def test_s3_key_layout():
    assert S3ModelSource("b", "models").key_for("m-1.0") == "models/m-1.0.tar.gz"
    assert S3ModelSource("b", "").key_for("m-1.0") == "m-1.0.tar.gz"


def test_local_source_resolves_without_copying(local_model_root, tmp_path):
    source = LocalModelSource(local_model_root)

    path = source.fetch(MODEL_NAME, tmp_path / "unused")

    assert (path / "config.cfg").is_file()
    assert source.size_bytes(MODEL_NAME) == 0
    assert not (tmp_path / "unused").exists()


def test_local_source_errors_when_the_model_is_absent(tmp_path):
    with pytest.raises(ModelUnavailableError):
        LocalModelSource(tmp_path / "nothing").fetch(MODEL_NAME, tmp_path)


def test_github_source_builds_the_release_url():
    url = GitHubModelSource().url_for("en_core_web_sm-3.7.1")
    assert url.endswith(
        "spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1.tar.gz"
    )


def test_registry_builds_the_configured_source(local_model_root):
    settings = Settings.from_env(
        {"MODEL_SOURCE": "local", "LOCAL_MODEL_ROOT": str(local_model_root)}
    )
    assert isinstance(build_model_source(settings), LocalModelSource)


def test_unknown_source_lists_the_known_ones():
    settings = Settings.from_env({"MODEL_SOURCE": "carrier-pigeon"})
    with pytest.raises(ConfigError, match="known sources"):
        build_model_source(settings)


def test_a_new_source_can_be_registered_without_touching_this_package(tmp_path):
    """The extension seam: EFS, an artifact store, a fixture — same one method."""

    class StubSource:
        name = "stub"

        def fetch(self, model_name, dest):
            return tmp_path

        def size_bytes(self, model_name):
            return None

    assert isinstance(StubSource(), ModelSource)  # structural, no base class needed
    register_model_source("stub", lambda settings: StubSource())
    try:
        assert "stub" in available_model_sources()
        built = build_model_source(Settings.from_env({"MODEL_SOURCE": "stub"}))
        assert built.fetch(MODEL_NAME, tmp_path) == tmp_path
    finally:
        from nlp_lambda.model_source import _REGISTRY

        _REGISTRY.pop("stub", None)
