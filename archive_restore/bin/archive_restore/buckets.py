"""Archive bucket discovery: parse, filter, dedupe and summarise (no Splunk imports).

Archive layout (written by org_frozen_app or coldToFrozenDir):

    {archive_root}/{index}/{bucket_name}/rawdata/journal.*   (+ optional rawdata/l2hash)

Bucket names follow Splunk's ``db_<latest>_<earliest>_<localid>[_<guid>]``
(``rb_`` for replicated copies). Times are epoch seconds.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

INDEX_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,79}$")
BUCKET_RE = re.compile(r"^(db|rb)_(\d{1,12})_(\d{1,12})_(\d{1,10})(?:_([0-9A-Fa-f-]{36}))?$")


class UnsafePathError(ValueError):
    """A path would resolve outside its allowed root."""


@dataclass(frozen=True)
class BucketName:
    name: str
    kind: str
    latest: int
    earliest: int
    local_id: int
    guid: Optional[str]

    @property
    def key(self) -> Tuple[int, int, int, Optional[str]]:
        """Identity shared by every replicated copy of the same bucket."""
        return (self.earliest, self.latest, self.local_id, self.guid)


@dataclass(frozen=True)
class Bucket:
    index: str
    bucket: BucketName
    path: Path
    size: int
    has_l2hash: bool

    @property
    def name(self) -> str:
        return self.bucket.name


def is_valid_index(name: str) -> bool:
    return bool(INDEX_RE.match(name))


def parse_bucket_name(name: str) -> Optional[BucketName]:
    """Return the parsed bucket name, or None if it is not a Splunk bucket name."""
    m = BUCKET_RE.match(name)
    if not m:
        return None
    kind, latest, earliest, local_id, guid = m.groups()
    return BucketName(name, kind, int(latest), int(earliest), int(local_id), guid)


def overlaps(b: BucketName, earliest: int, latest: int) -> bool:
    """True when the bucket's time span intersects [earliest, latest]."""
    return b.latest >= earliest and b.earliest <= latest


def safe_child(root: Path, name: str) -> Path:
    """Return root/name, refusing anything that is not a direct, real child of root."""
    if not name or name in (".", "..") or os.sep in name or (os.altsep and os.altsep in name):
        raise UnsafePathError("invalid path component: %r" % name)
    child = root / name
    if child.is_symlink():
        raise UnsafePathError("symlink not allowed: %s" % child)
    if child.resolve().parent != root.resolve():
        raise UnsafePathError("path escapes root: %s" % child)
    return child


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(str(path)):
        for f in files:
            fp = os.path.join(dirpath, f)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total


def has_symlink(path: Path) -> bool:
    for dirpath, dirs, files in os.walk(str(path)):
        for entry in dirs + files:
            if os.path.islink(os.path.join(dirpath, entry)):
                return True
    return False


def dedupe(buckets: Iterable[Bucket]) -> List[Bucket]:
    """Keep one copy per bucket identity, preferring db_ over rb_. Sorted by time."""
    best: Dict[Tuple[int, int, int, Optional[str]], Bucket] = {}
    for b in buckets:
        cur = best.get(b.bucket.key)
        if cur is None or (cur.bucket.kind == "rb" and b.bucket.kind == "db"):
            best[b.bucket.key] = b
    return sorted(best.values(), key=lambda b: (b.bucket.earliest, b.bucket.latest, b.name))


def list_indexes(archive_root: Path) -> List[str]:
    """Index directory names under archive_root (invalid names and symlinks skipped)."""
    if not archive_root.is_dir():
        raise FileNotFoundError("archive_root is not a directory: %s" % archive_root)
    return sorted(
        e.name
        for e in archive_root.iterdir()
        if e.is_dir() and not e.is_symlink() and is_valid_index(e.name)
    )


def scan_index(archive_root: Path, index: str) -> Tuple[List[Bucket], List[str]]:
    """Return (deduped buckets, skipped entry names) for one archived index."""
    if not is_valid_index(index):
        raise ValueError("invalid index name: %r" % index)
    index_dir = safe_child(archive_root, index)
    if not index_dir.is_dir():
        return [], []
    found: List[Bucket] = []
    skipped: List[str] = []
    for entry in index_dir.iterdir():
        parsed = parse_bucket_name(entry.name)
        if parsed is None or entry.is_symlink() or not entry.is_dir():
            skipped.append(entry.name)
            continue
        rawdata = entry / "rawdata"
        if not rawdata.is_dir() or not any(rawdata.glob("journal*")):
            skipped.append(entry.name)
            continue
        found.append(
            Bucket(
                index=index,
                bucket=parsed,
                path=entry,
                size=dir_size(entry),
                has_l2hash=(rawdata / "l2hash").is_file(),
            )
        )
    return dedupe(found), sorted(skipped)


def select(buckets: Iterable[Bucket], earliest: int, latest: int) -> List[Bucket]:
    return [b for b in buckets if overlaps(b.bucket, earliest, latest)]


def summarise(index: str, buckets: List[Bucket]) -> Dict[str, object]:
    """One summary row for an archived index."""
    return {
        "index": index,
        "buckets": len(buckets),
        "earliest": min((b.bucket.earliest for b in buckets), default=None),
        "latest": max((b.bucket.latest for b in buckets), default=None),
        "bytes": sum(b.size for b in buckets),
    }


def thawed_names(thawed_dir: Path) -> Set[str]:
    """Bucket directory names already present in a thaweddb."""
    if not thawed_dir.is_dir():
        return set()
    return {e.name for e in thawed_dir.iterdir() if parse_bucket_name(e.name) and e.is_dir()}
