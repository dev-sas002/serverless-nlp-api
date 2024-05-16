"""WSGI entrypoint.

Zappa (``app_function: app.app``) and serverless-wsgi both import this module and
look for a module-level ``app``. Everything it does is delegate to the application
factory — there is no logic here to go stale.
"""

from nlp_lambda.api import create_app
from nlp_lambda.config import Settings, load_dotenv_if_available

load_dotenv_if_available()

app = create_app(Settings.from_env())


if __name__ == "__main__":  # pragma: no cover - local convenience only
    import os

    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8320")))
