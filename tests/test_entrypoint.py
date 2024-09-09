"""The WSGI module Zappa imports must be importable with a bare environment.

This is the test the original code would have failed: ``import app`` read three
environment variables and downloaded a model at module scope.
"""

import importlib
import sys


def test_importing_app_needs_no_env_and_downloads_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_SOURCE", "local")
    monkeypatch.setenv("LOCAL_MODEL_ROOT", str(tmp_path))
    sys.modules.pop("app", None)

    module = importlib.import_module("app")

    assert module.app.name == "nlp_lambda.api"
    assert module.app.extensions["nlp_service"].is_loaded is False
    assert list(tmp_path.iterdir()) == []


def test_the_cli_parser_builds_without_any_configuration():
    from nlp_lambda.cli import build_parser

    args = build_parser().parse_args(["analyze", "hello", "--analyzer", "locations"])

    assert args.analyzer == "locations"
    assert args.text == "hello"
