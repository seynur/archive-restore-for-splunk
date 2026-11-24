import logging
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "archive_restore" / "bin"))

GUID = "1D7B6C3E-2F4A-4B5C-8D9E-0A1B2C3D4E5F"

STUB = """#!/bin/sh
echo "$@" >> "$SPLUNK_HOME/calls.log"
[ -n "$STUB_SLEEP" ] && sleep "$STUB_SLEEP"
case "$1" in
  rebuild) echo "rebuilding $2"; : > "$2.rbsentinel"; exit "${STUB_REBUILD_RC:-0}" ;;
  check-integrity) echo "${STUB_INTEGRITY_OUT:-succeeded=1, failed=0}"; exit 0 ;;
  btool) f="$SPLUNK_HOME/btool/$2.$4"; [ -f "$f" ] && cat "$f"; exit "${STUB_BTOOL_RC:-0}" ;;
esac
exit 2
"""


def make_bucket(root: Path, index: str, name: str, l2hash: bool = True, size: int = 10) -> Path:
    raw = root / index / name / "rawdata"
    raw.mkdir(parents=True)
    (raw / "journal.zst").write_bytes(b"x" * size)
    if l2hash:
        (raw / "l2hash").write_bytes(b"h")
    return root / index / name


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    return root


@pytest.fixture
def splunk_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "splunk"
    (home / "bin").mkdir(parents=True)
    (home / "var" / "log" / "splunk").mkdir(parents=True)
    stub = home / "bin" / "splunk"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SPLUNK_HOME", str(home))
    for var in ("STUB_SLEEP", "STUB_REBUILD_RC", "STUB_INTEGRITY_OUT", "STUB_BTOOL_RC"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def logger(splunk_home: Path) -> logging.Logger:
    from archive_restore.log import setup_logger

    lg = setup_logger("test-%d" % os.getpid())
    yield lg
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)
