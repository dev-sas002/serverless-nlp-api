"""HTTP layer: parse, validate, delegate, serialise. No linguistics in here.

Routes are thin on purpose — the interesting behaviour lives in
:mod:`nlp_lambda.service` and :mod:`nlp_lambda.model_cache`, both of which can be
tested without a WSGI server.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from flask import Blueprint, Flask, current_app, jsonify, request

from nlp_lambda import __version__
from nlp_lambda.bootstrap import build_service
from nlp_lambda.config import Settings
from nlp_lambda.errors import ConfigError, ModelUnavailableError, ValidationError
from nlp_lambda.service import NlpService, available_analyzers, describe_analyzer

logger = logging.getLogger(__name__)

bp = Blueprint("api", __name__)


def _service() -> NlpService:
    return current_app.extensions["nlp_service"]


def _texts_from_payload(payload: Any) -> list[str]:
    """Accept ``{"text": "..."}`` or ``{"texts": [...]}`` and nothing else.

    The original route did ``find_locations(**json_payload)``, which handed every
    key in an untrusted body straight to a Python call — an unexpected key was a
    ``TypeError`` and a 500 rather than a 400.
    """

    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    if "texts" in payload and "text" in payload:
        raise ValidationError("send either 'text' or 'texts', not both")
    if "texts" in payload:
        texts = payload["texts"]
        if not isinstance(texts, list):
            raise ValidationError("'texts' must be a list of strings")
        return texts
    if "text" in payload:
        return [payload["text"]]
    raise ValidationError("request body must contain 'text' or 'texts'")


@bp.get("/")
def index():
    """Service identity. Useful when a reviewer curls the root of a deployment."""

    return jsonify(
        {
            "service": "nlp-aws-lambda-hosting",
            "version": __version__,
            "routes": {
                "GET /health": "liveness; does not touch S3 or the model",
                "POST /warmup": "force the model load and report what it cost",
                "POST /analyze": "{text|texts, analyzer} -> one result per text",
                "POST /find_locations": "{text} -> place names, most frequent first",
            },
            "analyzers": {name: describe_analyzer(name) for name in available_analyzers()},
        }
    )


@bp.get("/health")
def health():
    """Liveness only. Deliberately does not touch the model or S3."""

    service = _service()
    return jsonify(
        {
            "status": "ok",
            "version": __version__,
            "model_loaded": service.is_loaded,
            "model": service.stats.extra.get("model_name"),
            "model_source": service.stats.extra.get("model_source"),
            "analyzers": list(available_analyzers()),
        }
    )


@bp.post("/warmup")
def warmup():
    """Pay the cold-start cost on purpose, on a schedule, instead of on a user."""

    started = time.perf_counter()
    details = _service().warm_up()
    details["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return jsonify(details)


@bp.post("/analyze")
def analyze():
    payload = request.get_json(silent=True)
    texts = _texts_from_payload(payload)
    analyzer = payload.get("analyzer", "entities")
    if not isinstance(analyzer, str):
        raise ValidationError("'analyzer' must be a string")
    results = _service().analyze(texts, analyzer=analyzer)
    return jsonify({"analyzer": analyzer, "count": len(results), "results": results})


@bp.post("/find_locations")
def find_locations():
    """The original endpoint, kept so existing callers do not break.

    It is now one analyzer among several rather than the only thing the service
    can do.
    """

    payload = request.get_json(silent=True)
    texts = _texts_from_payload(payload)
    results = _service().analyze(texts, analyzer="locations")
    if "texts" in (payload or {}):
        return jsonify(results)
    return jsonify(results[0])


def _init_sentry(settings: Settings) -> bool:
    """Wire Sentry only if a DSN is configured; never crash for the want of one."""

    if not settings.sentry_dsn:
        logger.info("sentry_disabled reason=no_dsn")
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(dsn=settings.sentry_dsn, integrations=[FlaskIntegration()])
        return True
    except ImportError:
        logger.warning("sentry_disabled reason=sentry_sdk_not_installed")
        return False


def create_app(settings: Settings | None = None, service: NlpService | None = None) -> Flask:
    """Application factory.

    Tests inject a ``service`` backed by a blank pipeline; Lambda calls it with no
    arguments and gets the S3-backed one.
    """

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.extensions["nlp_service"] = service or build_service(settings)
    app.extensions["sentry_enabled"] = _init_sentry(settings)
    app.register_blueprint(bp)

    @app.errorhandler(ValidationError)
    def _bad_request(exc: ValidationError):
        return jsonify({"error": "bad_request", "detail": str(exc)}), 400

    @app.errorhandler(ConfigError)
    def _misconfigured(exc: ConfigError):
        logger.error("configuration_error detail=%s", exc)
        return jsonify({"error": "misconfigured", "detail": str(exc)}), 500

    @app.errorhandler(ModelUnavailableError)
    def _model_unavailable(exc: ModelUnavailableError):
        logger.error("model_unavailable detail=%s", exc)
        return jsonify({"error": "model_unavailable", "detail": str(exc)}), 503

    @app.errorhandler(404)
    def _not_found(_):
        return jsonify({"error": "not_found"}), 404

    @app.errorhandler(405)
    def _not_allowed(_):
        return jsonify({"error": "method_not_allowed"}), 405

    return app
