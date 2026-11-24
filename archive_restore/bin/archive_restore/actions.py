"""List / restore / delete flows shared by the search commands and the CLI.

No Splunk SDK imports. The caller supplies how to find an index's thaweddb
(REST from a search command, btool from the CLI) as ``thawed_dir_fn``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List

from . import buckets as bk
from . import ops
from .config import Settings

Row = Dict[str, object]
ThawedDirFn = Callable[[str], Path]


class IndexNotFound(LookupError):
    """The index does not exist on this instance (or has no thawedPath)."""


def list_summary(archive_root: Path) -> Iterator[Row]:
    """One row per archived index."""
    for name in bk.list_indexes(archive_root):
        yield bk.summarise(name, bk.scan_index(archive_root, name)[0])


def list_buckets(
    archive_root: Path,
    indexes: Iterable[str],
    earliest: int,
    latest: int,
    thawed_dir_fn: ThawedDirFn,
) -> Iterator[Row]:
    """One row per archived bucket in the time range, with its thaw state."""
    for name in indexes:
        try:
            thawed = bk.thawed_names(thawed_dir_fn(name))
            missing = False
        except IndexNotFound:
            thawed, missing = set(), True
        for b in bk.select(bk.scan_index(archive_root, name)[0], earliest, latest):
            if missing:
                state = "index_missing"
            else:
                state = "thawed" if b.name in thawed else "archived"
            yield {
                "index": name,
                "bucket": b.name,
                "earliest": b.bucket.earliest,
                "latest": b.bucket.latest,
                "bytes": b.size,
                "has_l2hash": int(b.has_l2hash),
                "state": state,
            }


def plan_restore(
    settings: Settings,
    indexes: Iterable[str],
    earliest: int,
    latest: int,
    thawed_dir_fn: ThawedDirFn,
) -> List[ops.IndexPlan]:
    return [
        ops.IndexPlan(
            name,
            thawed_dir_fn(name),
            bk.select(bk.scan_index(settings.archive_root, name)[0], earliest, latest),
        )
        for name in indexes
    ]


def run_restore(
    plans: List[ops.IndexPlan],
    settings: Settings,
    logger: logging.Logger,
    user: str,
    dry_run: bool = False,
) -> Iterator[Row]:
    """Preflight, then restore under the run lock. Raises PreflightError before any change."""
    ops.preflight(plans, settings)
    with ops.exclusive_lock(ops.lock_path()):
        for plan in plans:
            yield from ops.restore_buckets(plan, settings, logger, user, dry_run)


def delete_targets(thawed: Path, bucket: str, earliest: int, latest: int) -> List[str]:
    """A named bucket as-is (validated on delete), or with ``*`` every thawed one in range."""
    if bucket != "*":
        return [bucket]
    names = []
    for n in bk.thawed_names(thawed):
        parsed = bk.parse_bucket_name(n)
        if parsed is not None and bk.overlaps(parsed, earliest, latest):
            names.append(n)
    return sorted(names)


def run_delete(
    index: str, thawed: Path, targets: List[str], logger: logging.Logger, user: str
) -> Iterator[Row]:
    with ops.exclusive_lock(ops.lock_path()):
        yield from ops.delete_thawed(index, thawed, targets, logger, user)
