"""Tar extraction and spaCy model-directory discovery.

Both concerns are shared by every :class:`~nlp_lambda.model_source.ModelSource`
that ships models as ``.tar.gz``, so they live here rather than in any one source.
"""

from __future__ import annotations

import tarfile
from collections.abc import Iterable
from pathlib import Path

from nlp_lambda.errors import ModelUnavailableError, UnsafeArchiveError

#: Files that mark a directory as a loadable spaCy pipeline.
_V3_MARKER = "config.cfg"
_V2_MARKERS = ("meta.json", "tokenizer")

_MAX_SEARCH_DEPTH = 4


def safe_extract(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest``, refusing members that escape it.

    ``TarFile.extractall`` will happily write to ``../../etc/cron.d`` if the archive
    says so (CVE-2007-4559). Python 3.12 defaults to the ``data`` filter, but this
    project also targets 3.9-3.11 where the default is still the unsafe one, so the
    check is made explicit instead of inherited.
    """

    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if target != dest and dest not in target.parents:
                raise UnsafeArchiveError(
                    f"archive member {member.name!r} would extract outside {dest}"
                )
            if member.issym() or member.islnk():
                link_target = (target.parent / member.linkname).resolve()
                if link_target != dest and dest not in link_target.parents:
                    raise UnsafeArchiveError(f"archive link {member.name!r} points outside {dest}")
        try:
            tar.extractall(path=dest, filter="data")  # type: ignore[call-arg]
        except TypeError:  # Python < 3.12 has no extraction filters
            tar.extractall(path=dest)  # noqa: S202 - members validated above


def _is_pipeline_dir(path: Path) -> bool:
    if (path / _V3_MARKER).is_file():
        return True
    return all((path / marker).exists() for marker in _V2_MARKERS)


def _walk(root: Path, depth: int) -> Iterable[Path]:
    """Breadth-first directory walk, shallowest first, bounded depth."""

    level = [root]
    for _ in range(depth + 1):
        if not level:
            return
        yield from level
        level = [child for parent in level for child in sorted(parent.iterdir()) if child.is_dir()]


def find_model_dir(root: Path) -> Path:
    """Return the loadable pipeline directory inside an extracted model tree.

    A spaCy model archive unpacks to ``<name>-<version>/<name>/<name>-<version>/``:
    the outer directory is a Python sdist, only the inner one can be passed to
    ``spacy.load``. The original code rebuilt that path by string surgery on the
    model name, which broke for any model whose name did not contain exactly one
    ``-``. Looking for the marker files works for v2 and v3 layouts alike.
    """

    root = Path(root)
    if not root.is_dir():
        raise ModelUnavailableError(f"{root} is not a directory")
    deepest: Path | None = None
    for candidate in _walk(root, _MAX_SEARCH_DEPTH):
        if _is_pipeline_dir(candidate):
            deepest = candidate
    if deepest is None:
        raise ModelUnavailableError(
            f"no spaCy pipeline found under {root} "
            f"(looked for {_V3_MARKER} or {'+'.join(_V2_MARKERS)})"
        )
    return deepest
