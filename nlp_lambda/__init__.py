"""Run spaCy NLP on AWS Lambda with the model held in S3, not in the deployment package.

The public surface is deliberately small:

* :func:`nlp_lambda.api.create_app` — the Flask/WSGI entrypoint.
* :class:`nlp_lambda.service.NlpService` — the NLP work, with no Flask and no AWS in it.
* :class:`nlp_lambda.model_source.ModelSource` — the seam that decides *where a model
  comes from* (S3 today, a local directory or EFS tomorrow).
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
