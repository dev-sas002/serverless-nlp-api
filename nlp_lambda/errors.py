"""Exception types shared across the package.

Keeping these in one small module means the HTTP layer can map failures to status
codes without importing the S3 client or spaCy.
"""

from __future__ import annotations


class NlpLambdaError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(NlpLambdaError):
    """Configuration is missing or contradictory.

    Raised late (when the offending setting is first needed) rather than at import
    time, so that ``/health`` still answers on a misconfigured deployment.
    """


class ModelUnavailableError(NlpLambdaError):
    """The model could not be fetched, extracted or located on disk."""


class InsufficientSpaceError(ModelUnavailableError):
    """The cache directory does not have room for the model.

    On Lambda the cache directory is ``/tmp``, which is 512 MB by default. Failing
    with this instead of an opaque ``OSError: [Errno 28]`` halfway through a
    ``tarfile`` extraction makes the 512 MB ceiling visible in CloudWatch.
    """


class UnsafeArchiveError(ModelUnavailableError):
    """A tar member tried to escape the extraction directory."""


class ValidationError(NlpLambdaError):
    """The caller sent a request body this service will not process."""
