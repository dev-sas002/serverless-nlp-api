import pytest

from nlp_lambda.api import create_app
from nlp_lambda.config import Settings
from nlp_lambda.service import NlpService


@pytest.fixture
def client(blank_nlp):
    service = NlpService(lambda: blank_nlp, max_batch_size=3, max_text_chars=200)
    app = create_app(Settings.from_env({"SPACY_MODEL": "en_test-1.0.0"}), service=service)
    app.config.update(TESTING=True)
    return app.test_client()


def test_health_does_not_load_the_model(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json["status"] == "ok"
    assert response.json["model_loaded"] is False
    assert "locations" in response.json["analyzers"]


def test_warmup_loads_the_model_and_reports_the_cost(client):
    response = client.post("/warmup")

    assert response.status_code == 200
    assert response.json["already_warm"] is False
    assert response.json["elapsed_seconds"] >= 0
    assert client.get("/health").json["model_loaded"] is True


def test_analyze_single_text(client):
    response = client.post("/analyze", json={"text": "Ada Lovelace visited Madrid"})

    assert response.status_code == 200
    assert response.json["count"] == 1
    labels = {e["label"] for e in response.json["results"][0]}
    assert labels == {"PERSON", "GPE"}


def test_analyze_batch(client):
    response = client.post("/analyze", json={"texts": ["Madrid", "Spain"], "analyzer": "locations"})

    assert response.json["results"] == [["Madrid"], ["Spain"]]


def test_find_locations_keeps_its_original_contract(client):
    response = client.post("/find_locations", json={"text": "Spain and the UK"})

    assert response.status_code == 200
    assert response.json == ["Spain", "the UK"]


def test_unexpected_keys_are_a_400_not_a_500(client):
    """`find_locations(**payload)` used to hand request keys to a Python call."""

    response = client.post("/find_locations", json={"txt": "typo", "verbose": True})

    assert response.status_code == 400
    assert response.json["error"] == "bad_request"


def test_missing_body_is_a_400(client):
    response = client.post("/analyze", data="not json", content_type="text/plain")

    assert response.status_code == 400
    assert response.json["error"] == "bad_request"


def test_oversized_batch_is_a_400(client):
    response = client.post("/analyze", json={"texts": ["a", "b", "c", "d"]})

    assert response.status_code == 400
    assert "max_batch_size" in response.json["detail"]


def test_unknown_analyzer_is_a_400(client):
    response = client.post("/analyze", json={"text": "hi", "analyzer": "astrology"})

    assert response.status_code == 400


def test_unknown_route_returns_json_not_html(client):
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json == {"error": "not_found"}


def test_model_download_failure_is_a_503(blank_nlp):
    from nlp_lambda.errors import ModelUnavailableError

    def broken_loader():
        raise ModelUnavailableError("s3://bucket/models/x.tar.gz not found")

    app = create_app(Settings.from_env({}), service=NlpService(broken_loader))
    app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

    response = app.test_client().post("/analyze", json={"text": "hello"})

    assert response.status_code == 503
    assert response.json["error"] == "model_unavailable"


def test_sentry_is_skipped_when_no_dsn_is_configured(blank_nlp):
    """The old app.py did os.environ['SENTRY_DSN'] at import and died without it."""

    app = create_app(Settings.from_env({}), service=NlpService(lambda: blank_nlp))

    assert app.extensions["sentry_enabled"] is False


def test_root_describes_the_service(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json["service"] == "nlp-aws-lambda-hosting"
    assert "POST /analyze" in response.json["routes"]
    assert response.json["analyzers"]["locations"]
    # Describing the service must not cost a model load.
    assert client.get("/health").json["model_loaded"] is False
