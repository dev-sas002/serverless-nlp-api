"""Where a spaCy model comes from — the project's one extension seam.

The whole point of this service is that the model does *not* live in the Lambda
package. Something has to fetch it at runtime, and which "something" is exactly the
decision most likely to change: S3 today, a mounted EFS volume when cold starts
matter more than storage cost, a baked-in directory in local development or in a
container image, a private artifact store at a company that does not allow public
model downloads.

So the fetch is a one-method protocol with a registry in front of it::

    register_model_source("efs", lambda settings: LocalModelSource(Path("/mnt/models")))

Nothing above this module knows which implementation it got.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from nlp_lambda.archive import find_model_dir, safe_extract
from nlp_lambda.config import Settings
from nlp_lambda.errors import ConfigError, ModelUnavailableError

GITHUB_RELEASES = "https://github.com/explosion/spacy-models/releases/download"


@runtime_checkable
class ModelSource(Protocol):
    """Fetches a named spaCy model and returns a directory ``spacy.load`` accepts."""

    #: Short identifier used in logs and in ``MODEL_SOURCE``.
    name: str

    def fetch(self, model_name: str, dest: Path) -> Path:
        """Make ``model_name`` available under ``dest`` and return the pipeline dir."""

    def size_bytes(self, model_name: str) -> int | None:
        """Compressed size of the model, or ``None`` if it cannot be known cheaply.

        Used to check the download against the free space in ``/tmp`` *before*
        starting it.
        """


class LocalModelSource:
    """Read a model that is already on the filesystem.

    Used by the test suite, by ``docker compose`` when a model is mounted, and by
    anyone putting models on EFS: the fetch is then a no-op path resolution.
    """

    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def fetch(self, model_name: str, dest: Path) -> Path:  # noqa: ARG002 - protocol shape
        candidate = self.root / model_name
        if not candidate.exists():
            # Allow LOCAL_MODEL_ROOT to point straight at a pipeline directory.
            candidate = self.root
        if not candidate.exists():
            raise ModelUnavailableError(f"no model {model_name!r} under {self.root}")
        return find_model_dir(candidate)

    def size_bytes(self, model_name: str) -> int | None:  # noqa: ARG002 - protocol shape
        return 0  # nothing is copied, so nothing is consumed


class S3ModelSource:
    """Download ``s3://<bucket>/<prefix>/<model>.tar.gz`` and unpack it.

    This is the production path and the reason the project exists: the 250 MB
    unzipped Lambda package limit is not big enough for ``en_core_web_md`` and its
    dependencies, but S3 has no such limit and ``/tmp`` gives us 512 MB to unpack
    into.
    """

    name = "s3"

    def __init__(
        self,
        bucket: str,
        prefix: str = "models",
        *,
        endpoint_url: str | None = None,
        client: object | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._endpoint_url = endpoint_url
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3  # imported lazily: the local source needs no AWS SDK

            self._client = boto3.client("s3", endpoint_url=self._endpoint_url)
        return self._client

    def key_for(self, model_name: str) -> str:
        return f"{self.prefix}/{model_name}.tar.gz" if self.prefix else f"{model_name}.tar.gz"

    def fetch(self, model_name: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        archive = dest / f"{model_name}.tar.gz"
        key = self.key_for(model_name)
        try:
            self.client.download_file(self.bucket, key, str(archive))
        except Exception as exc:  # botocore raises a family of ClientErrors
            raise ModelUnavailableError(
                f"could not download s3://{self.bucket}/{key}: {exc}"
            ) from exc
        try:
            safe_extract(archive, dest)
            return find_model_dir(dest)
        finally:
            # The tarball is dead weight in a 512 MB /tmp once unpacked.
            archive.unlink(missing_ok=True)

    def size_bytes(self, model_name: str) -> int | None:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self.key_for(model_name))
        except Exception:
            return None
        return int(head["ContentLength"])


class GitHubModelSource:
    """Download a release tarball from ``explosion/spacy-models``.

    Not used at request time — Lambda should not depend on GitHub being up. It
    exists so ``python -m nlp_lambda.cli upload`` can seed the S3 bucket, and so a
    developer can populate a local cache without AWS.
    """

    name = "github"

    def __init__(self, base_url: str = GITHUB_RELEASES, session: object | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session

    def url_for(self, model_name: str) -> str:
        return f"{self.base_url}/{model_name}/{model_name}.tar.gz"

    def download_archive(self, model_name: str, dest: Path) -> Path:
        import requests

        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        archive = dest / f"{model_name}.tar.gz"
        session = self._session or requests
        with session.get(self.url_for(model_name), stream=True, timeout=60) as response:
            response.raise_for_status()
            with open(archive, "wb") as handle:
                shutil.copyfileobj(response.raw, handle)
        return archive

    def fetch(self, model_name: str, dest: Path) -> Path:
        archive = self.download_archive(model_name, dest)
        try:
            safe_extract(archive, Path(dest))
            return find_model_dir(Path(dest))
        finally:
            archive.unlink(missing_ok=True)

    def size_bytes(self, model_name: str) -> int | None:  # noqa: ARG002 - protocol shape
        return None


SourceFactory = Callable[[Settings], ModelSource]

_REGISTRY: dict[str, SourceFactory] = {
    "s3": lambda s: S3ModelSource(
        s.require_s3_bucket(), s.s3_prefix, endpoint_url=s.s3_endpoint_url
    ),
    "local": lambda s: LocalModelSource(s.require_local_model_root()),
    "github": lambda _settings: GitHubModelSource(),
}


def register_model_source(name: str, factory: SourceFactory) -> None:
    """Add or replace a model source. The seam a future developer reaches for."""

    _REGISTRY[name.strip().lower()] = factory


def available_model_sources() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build_model_source(settings: Settings) -> ModelSource:
    try:
        factory = _REGISTRY[settings.model_source]
    except KeyError as exc:
        raise ConfigError(
            f"unknown MODEL_SOURCE={settings.model_source!r}; "
            f"known sources: {', '.join(available_model_sources())}"
        ) from exc
    return factory(settings)
