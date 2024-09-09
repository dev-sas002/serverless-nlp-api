"""The operator CLI. These used to be commented-out lines under ``if __name__``."""

import pytest

from nlp_lambda import cli


@pytest.fixture
def recorded_downloads(monkeypatch, tmp_path):
    """Replace the GitHub download with a recorder — no network in tests."""

    seen = []

    def fake_download(self, model_name, dest):
        seen.append(model_name)
        archive = tmp_path / f"{model_name}.tar.gz"
        archive.write_bytes(b"not really a tarball")
        return archive

    monkeypatch.setattr(cli.GitHubModelSource, "download_archive", fake_download)
    return seen


def test_download_uses_the_configured_model(monkeypatch, recorded_downloads, tmp_path, capsys):
    monkeypatch.setenv("SPACY_MODEL", "en_core_web_sm-3.8.0")

    assert cli.main(["download", "--dest", str(tmp_path)]) == 0

    assert recorded_downloads == ["en_core_web_sm-3.8.0"]
    assert "en_core_web_sm-3.8.0.tar.gz" in capsys.readouterr().out


def test_the_model_flag_overrides_the_environment(monkeypatch, recorded_downloads, tmp_path):
    monkeypatch.setenv("SPACY_MODEL", "en_core_web_sm-3.8.0")

    cli.main(["--model", "xx_ent_wiki_sm-3.7.0", "download", "--dest", str(tmp_path)])

    assert recorded_downloads == ["xx_ent_wiki_sm-3.7.0"]


def test_upload_without_a_bucket_fails_with_a_message_not_a_traceback(
    recorded_downloads, tmp_path, capsys
):
    assert cli.main(["upload", "--dest", str(tmp_path)]) == 1
    assert "S3_BUCKET" in capsys.readouterr().err
