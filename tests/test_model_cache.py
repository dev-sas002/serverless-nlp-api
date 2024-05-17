import pytest

from nlp_lambda.errors import InsufficientSpaceError, ModelUnavailableError
from nlp_lambda.model_cache import ModelCache
from nlp_lambda.model_source import LocalModelSource
from tests.conftest import MODEL_NAME, build_model_tree


class CountingSource:
    """A model source that records how often it was actually asked to fetch."""

    name = "counting"

    def __init__(self, staging_root, size=1024):
        self.staging_root = staging_root
        self.calls = 0
        self.size = size

    def fetch(self, model_name, dest):
        self.calls += 1
        build_model_tree(dest, model_name)
        from nlp_lambda.archive import find_model_dir

        return find_model_dir(dest / model_name)

    def size_bytes(self, model_name):
        return self.size


def test_first_call_fetches_and_lands_under_the_model_name(tmp_path):
    source = CountingSource(tmp_path)
    cache = ModelCache(source, tmp_path / "cache")

    result = cache.ensure(MODEL_NAME)

    assert result.cache_hit is False
    assert source.calls == 1
    assert (result.path / "config.cfg").is_file()
    assert (tmp_path / "cache" / MODEL_NAME).is_dir()
    assert result.bytes_on_disk > 0


def test_second_call_in_the_same_process_does_not_fetch_again(tmp_path):
    """The warm-invocation path: no S3 call, no unpack, no disk walk."""

    source = CountingSource(tmp_path)
    cache = ModelCache(source, tmp_path / "cache")

    cache.ensure(MODEL_NAME)
    warm = cache.ensure(MODEL_NAME)

    assert source.calls == 1
    assert warm.cache_hit is True


def test_a_fresh_process_reuses_bytes_already_in_tmp(tmp_path):
    """Same container, cold memo: the download must not be repeated."""

    source = CountingSource(tmp_path)
    ModelCache(source, tmp_path / "cache").ensure(MODEL_NAME)

    second_process = ModelCache(source, tmp_path / "cache")
    result = second_process.ensure(MODEL_NAME)

    assert source.calls == 1
    assert result.cache_hit is True
    assert (result.path / "config.cfg").is_file()


def test_no_staging_directory_is_left_behind(tmp_path):
    cache = ModelCache(CountingSource(tmp_path), tmp_path / "cache")
    cache.ensure(MODEL_NAME)

    leftovers = list((tmp_path / "cache").glob(".staging-*"))
    assert leftovers == []


def test_a_failed_fetch_leaves_nothing_half_written(tmp_path):
    class BrokenSource:
        name = "broken"

        def fetch(self, model_name, dest):
            (dest / "partial.bin").write_bytes(b"half a model")
            raise ModelUnavailableError("connection reset")

        def size_bytes(self, model_name):
            return None

    cache = ModelCache(BrokenSource(), tmp_path / "cache")

    with pytest.raises(ModelUnavailableError):
        cache.ensure(MODEL_NAME)

    assert not (tmp_path / "cache" / MODEL_NAME).exists()
    assert list((tmp_path / "cache").glob(".staging-*")) == []


def test_a_model_too_big_for_tmp_fails_before_the_download_starts(tmp_path):
    """The 512 MB /tmp ceiling, surfaced as a sentence instead of an ENOSPC."""

    source = CountingSource(tmp_path, size=400 * 1024 * 1024)
    cache = ModelCache(source, tmp_path / "cache", reserve_bytes=10**15)

    with pytest.raises(InsufficientSpaceError, match="512 MB"):
        cache.ensure(MODEL_NAME)

    assert source.calls == 0


def test_local_source_is_used_in_place_rather_than_copied(local_model_root, tmp_path):
    cache = ModelCache(LocalModelSource(local_model_root), tmp_path / "cache")

    result = cache.ensure(MODEL_NAME)

    assert result.path.is_relative_to(local_model_root)
    assert list((tmp_path / "cache").glob(".staging-*")) == []


def test_stats_describe_the_cache_without_loading_anything(tmp_path):
    cache = ModelCache(CountingSource(tmp_path), tmp_path / "cache")

    assert cache.stats()["cached_models"] == []
    cache.ensure(MODEL_NAME)
    stats = cache.stats()

    assert stats["cached_models"] == [MODEL_NAME]
    assert stats["source"] == "counting"
    assert stats["free_bytes"] > 0
