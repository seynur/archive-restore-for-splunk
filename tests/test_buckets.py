import os

import pytest

from archive_restore import buckets as bk
from conftest import GUID, make_bucket


@pytest.mark.parametrize(
    "name,expected",
    [
        ("db_200_100_7", ("db", 200, 100, 7, None)),
        ("rb_200_100_7_" + GUID, ("rb", 200, 100, 7, GUID)),
        ("db_1700000000_1690000000_12_" + GUID, ("db", 1700000000, 1690000000, 12, GUID)),
    ],
)
def test_parse_valid_names(name, expected):
    p = bk.parse_bucket_name(name)
    assert (p.kind, p.latest, p.earliest, p.local_id, p.guid) == expected


@pytest.mark.parametrize(
    "name",
    [
        "",
        "db_1_2",
        "hot_v1_3",
        "wineventlog_200_100_7",
        "db_200_100_7_../../x",
        "db_200_100_7_" + GUID + "_x",
        "../db_200_100_7",
        "inflight-db_200_100_7",
    ],
)
def test_parse_rejects_junk(name):
    assert bk.parse_bucket_name(name) is None


@pytest.mark.parametrize(
    "earliest,latest,hit",
    [
        (0, 99, False),
        (0, 100, True),
        (150, 160, True),
        (200, 300, True),
        (201, 300, False),
        (50, 250, True),
    ],
)
def test_overlaps_boundaries(earliest, latest, hit):
    assert bk.overlaps(bk.parse_bucket_name("db_200_100_1"), earliest, latest) is hit


@pytest.mark.parametrize("name", ["Bad", "a b", "../x", "-x", "x/y", ""])
def test_invalid_index_names(name):
    assert not bk.is_valid_index(name)


def test_scan_dedupes_rb_and_prefers_db(archive):
    make_bucket(archive, "idx", "rb_200_100_7_" + GUID)
    make_bucket(archive, "idx", "db_200_100_7_" + GUID)
    make_bucket(archive, "idx", "db_400_300_8_" + GUID)
    found, skipped = bk.scan_index(archive, "idx")
    assert [b.name for b in found] == ["db_200_100_7_" + GUID, "db_400_300_8_" + GUID]
    assert skipped == []


def test_scan_skips_junk_missing_journal_and_symlinks(archive, tmp_path):
    make_bucket(archive, "idx", "db_200_100_7")
    (archive / "idx" / "notes.txt").write_text("x")
    (archive / "idx" / "db_300_250_9" / "rawdata").mkdir(parents=True)  # no journal
    outside = make_bucket(tmp_path, "elsewhere", "db_500_400_1")
    os.symlink(str(outside), str(archive / "idx" / "db_500_400_1"))
    found, skipped = bk.scan_index(archive, "idx")
    assert [b.name for b in found] == ["db_200_100_7"]
    assert skipped == ["db_300_250_9", "db_500_400_1", "notes.txt"]


def test_scan_size_and_l2hash(archive):
    make_bucket(archive, "idx", "db_200_100_7", l2hash=False, size=42)
    (b,) = bk.scan_index(archive, "idx")[0]
    assert b.size == 42 and b.has_l2hash is False


def test_scan_missing_index_is_empty(archive):
    assert bk.scan_index(archive, "nope") == ([], [])


def test_scan_rejects_invalid_index(archive):
    with pytest.raises(ValueError):
        bk.scan_index(archive, "../etc")


def test_list_indexes_skips_symlinks_and_bad_names(archive, tmp_path):
    (archive / "good_idx").mkdir()
    (archive / "Bad Name").mkdir()
    (tmp_path / "other").mkdir()
    os.symlink(str(tmp_path / "other"), str(archive / "linked"))
    assert bk.list_indexes(archive) == ["good_idx"]


def test_list_indexes_missing_root(tmp_path):
    with pytest.raises(FileNotFoundError):
        bk.list_indexes(tmp_path / "missing")


def test_select_and_summarise(archive):
    make_bucket(archive, "idx", "db_200_100_1", size=5)
    make_bucket(archive, "idx", "db_400_300_2", size=7)
    found = bk.scan_index(archive, "idx")[0]
    assert [b.name for b in bk.select(found, 250, 350)] == ["db_400_300_2"]
    assert bk.summarise("idx", found) == {
        "index": "idx",
        "buckets": 2,
        "earliest": 100,
        "latest": 400,
        "bytes": 5 + 1 + 7 + 1,
    }
    assert bk.summarise("empty", [])["earliest"] is None


@pytest.mark.parametrize("name", ["..", ".", "a/b", "", "../x"])
def test_safe_child_rejects_traversal(tmp_path, name):
    with pytest.raises(bk.UnsafePathError):
        bk.safe_child(tmp_path, name)


def test_safe_child_rejects_symlink(tmp_path):
    os.symlink("/etc", str(tmp_path / "db_1_1_1"))
    with pytest.raises(bk.UnsafePathError):
        bk.safe_child(tmp_path, "db_1_1_1")


def test_thawed_names_only_buckets(tmp_path):
    (tmp_path / "db_2_1_1").mkdir()
    (tmp_path / ".archive_restore_staging").mkdir()
    (tmp_path / "junk").mkdir()
    assert bk.thawed_names(tmp_path) == {"db_2_1_1"}
    assert bk.thawed_names(tmp_path / "missing") == set()
