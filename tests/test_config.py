import pytest

from nlp_lambda.config import DEFAULT_MODEL, Settings
from nlp_lambda.errors import ConfigError


def test_defaults_do_not_require_any_environment():
    """Importing and configuring must not depend on a .env file existing."""

    settings = Settings.from_env({})
    assert settings.model_name == DEFAULT_MODEL
    assert settings.model_source == "s3"
    assert str(settings.cache_dir) == "/tmp/models"
    assert settings.sentry_dsn is None


def test_legacy_model_variable_is_still_read():
    settings = Settings.from_env({"SPACY_MODEL_MEDIUM": "en_core_web_md-3.7.1"})
    assert settings.model_name == "en_core_web_md-3.7.1"


def test_new_model_variable_wins_over_legacy():
    settings = Settings.from_env({"SPACY_MODEL": "a-1.0.0", "SPACY_MODEL_MEDIUM": "b-1.0.0"})
    assert settings.model_name == "a-1.0.0"


def test_numeric_settings_are_parsed():
    settings = Settings.from_env({"MAX_BATCH_SIZE": "4", "MAX_TEXT_CHARS": "10"})
    assert settings.max_batch_size == 4
    assert settings.max_text_chars == 10


def test_bad_number_is_reported_as_a_config_error():
    with pytest.raises(ConfigError, match="MAX_BATCH_SIZE"):
        Settings.from_env({"MAX_BATCH_SIZE": "lots"})


def test_s3_prefix_is_normalised():
    assert Settings.from_env({"S3_PREFIX": "/spacy/models/"}).s3_prefix == "spacy/models"


def test_missing_bucket_is_a_config_error_not_a_key_error():
    with pytest.raises(ConfigError, match="S3_BUCKET"):
        Settings.from_env({}).require_s3_bucket()


def test_missing_local_root_is_a_config_error():
    with pytest.raises(ConfigError, match="LOCAL_MODEL_ROOT"):
        Settings.from_env({"MODEL_SOURCE": "local"}).require_local_model_root()
