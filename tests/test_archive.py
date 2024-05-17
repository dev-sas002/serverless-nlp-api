import tarfile

import pytest

from nlp_lambda.archive import find_model_dir, safe_extract
from nlp_lambda.errors import ModelUnavailableError, UnsafeArchiveError
from tests.conftest import MODEL_NAME, build_model_tree


def test_extract_then_find_the_loadable_directory(model_archive, tmp_path):
    dest = tmp_path / "out"
    safe_extract(model_archive, dest)

    found = find_model_dir(dest)

    assert (found / "config.cfg").is_file()
    # The inner directory, not the sdist wrapper that also carries a meta.json.
    assert found.parent.parent.name == MODEL_NAME


def test_find_model_dir_handles_a_spacy_v2_layout(tmp_path):
    """v2 pipelines have no config.cfg; meta.json + tokenizer identify them."""

    inner = tmp_path / "en_core_web_sm-2.3.1" / "en_core_web_sm" / "en_core_web_sm-2.3.1"
    inner.mkdir(parents=True)
    (inner / "meta.json").write_text("{}")
    (inner / "tokenizer").write_bytes(b"\x00")
    (tmp_path / "en_core_web_sm-2.3.1" / "meta.json").write_text("{}")

    assert find_model_dir(tmp_path) == inner


def test_find_model_dir_copes_with_extra_hyphens_in_the_name(tmp_path):
    """The old code did model.split('-')[0] and broke on names like this."""

    build_model_tree(tmp_path, "xx_ent_wiki_sm-3.7.0")

    assert find_model_dir(tmp_path).name == "xx_ent_wiki_sm-3.7.0"


def test_missing_pipeline_is_reported_clearly(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ModelUnavailableError, match="no spaCy pipeline"):
        find_model_dir(tmp_path / "empty")


def test_path_traversal_member_is_rejected(tmp_path):
    """CVE-2007-4559: a tar member may name ../../somewhere-else."""

    evil = tmp_path / "evil.tar.gz"
    victim = tmp_path / "payload.txt"
    victim.write_text("pwned")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(victim, arcname="../../escaped.txt")

    with pytest.raises(UnsafeArchiveError, match="outside"):
        safe_extract(evil, tmp_path / "dest")

    assert not (tmp_path.parent / "escaped.txt").exists()


def test_absolute_path_member_is_rejected(tmp_path):
    evil = tmp_path / "abs.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as tar:
        info = tar.gettarinfo(payload)
        # tarfile strips a leading "/" from arcname, so set the raw name instead.
        info.name = "/etc/evil.txt"
        with open(payload, "rb") as handle:
            tar.addfile(info, handle)

    with pytest.raises(UnsafeArchiveError):
        safe_extract(evil, tmp_path / "dest2")
