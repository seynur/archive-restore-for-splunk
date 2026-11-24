from pathlib import Path

import pytest

from archive_restore.config import ConfigError, parse_settings


def test_defaults():
    s = parse_settings({"archive_root": "/archive"})
    assert s.archive_root == Path("/archive")
    assert s.max_buckets_per_run == 200 and s.check_integrity is True and s.space_factor == 5.0


def test_overrides():
    s = parse_settings(
        {
            "archive_root": "/a",
            "max_buckets_per_run": "5",
            "check_integrity": "0",
            "space_factor": "1.5",
            "rebuild_timeout": "60",
        }
    )
    assert (s.max_buckets_per_run, s.check_integrity, s.space_factor, s.rebuild_timeout) == (
        5,
        False,
        1.5,
        60,
    )


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"archive_root": ""},
        {"archive_root": "relative/path"},
        {"archive_root": "/a", "max_buckets_per_run": "0"},
        {"archive_root": "/a", "max_buckets_per_run": "lots"},
        {"archive_root": "/a", "space_factor": "0.5"},
        {"archive_root": "/a", "check_integrity": "maybe"},
    ],
)
def test_invalid_fails_closed(raw):
    with pytest.raises(ConfigError):
        parse_settings(raw)
