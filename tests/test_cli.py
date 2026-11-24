import json
import logging
import os

import pytest

import archive_restore_cli as cli
from archive_restore import local_ctx as lc
from conftest import make_bucket


def btool_file(splunk_home, conf, stanza, body):
    d = splunk_home / "btool"
    d.mkdir(exist_ok=True)
    (d / ("%s.%s" % (conf, stanza))).write_text("[%s]\n%s" % (stanza, body))


@pytest.fixture
def instance(archive, splunk_home, tmp_path, monkeypatch):
    """An installed app (archive_root) and index `idx` with thawedPath under $SPLUNK_DB."""
    db = tmp_path / "db"
    (db / "idx" / "thaweddb").mkdir(parents=True)
    monkeypatch.setenv("SPLUNK_DB", str(db))
    btool_file(splunk_home, "archive_restore", "settings", "archive_root = %s\n" % archive)
    btool_file(
        splunk_home,
        "indexes",
        "idx",
        "federated.dataset = \nthawedPath = $SPLUNK_DB/idx/thaweddb\n",
    )
    make_bucket(archive, "idx", "db_200_100_1")
    make_bucket(archive, "idx", "db_400_300_2")
    return db / "idx" / "thaweddb"


@pytest.fixture(autouse=True)
def fresh_cli_logger():
    # setup_logger caches its handler per process; each test has its own SPLUNK_HOME.
    yield
    lg = logging.getLogger("archive_restore.cli")
    for h in list(lg.handlers):
        h.close()
        lg.removeHandler(h)


def run_json(capsys, *argv):
    rc = cli.main(list(argv) + ["--json"])
    out = capsys.readouterr().out
    return rc, [json.loads(line) for line in out.splitlines()]


# --- local_ctx ---------------------------------------------------------------


def test_btool_reads_only_its_stanza(splunk_home):
    btool_file(
        splunk_home, "indexes", "idx", "thawedPath = /t\nkey = a=b\n[other]\nthawedPath = /x\n"
    )
    assert lc.btool("indexes", "idx") == {"thawedPath": "/t", "key": "a=b"}
    assert lc.btool("indexes", "missing") == {}


def test_btool_failure_fails_closed(splunk_home, monkeypatch):
    monkeypatch.setenv("STUB_BTOOL_RC", "1")
    with pytest.raises(lc.LocalContextError, match="btool"):
        lc.btool("indexes", "idx")


def test_thawed_dir_expands_splunk_db(instance):
    assert lc.thawed_dir("idx") == instance


def test_thawed_dir_unknown_index(instance):
    with pytest.raises(lc.UnknownIndex):
        lc.thawed_dir("nope")


def test_thawed_dir_unresolved_variable_fails(instance, monkeypatch):
    monkeypatch.delenv("SPLUNK_DB")
    with pytest.raises(lc.LocalContextError, match="splunk cmd"):
        lc.thawed_dir("idx")


def test_load_settings_missing_app(splunk_home):
    with pytest.raises(lc.LocalContextError, match="not found"):
        lc.load_settings()


def test_check_owner(tmp_path, monkeypatch):
    lc.check_owner([tmp_path])
    monkeypatch.setattr(lc.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(lc.LocalContextError, match="run as the user"):
        lc.check_owner([tmp_path])


# --- CLI ---------------------------------------------------------------------


def test_list_summary_and_buckets(instance, capsys):
    rc, rows = run_json(capsys, "list")
    assert rc == 0 and rows[0]["index"] == "idx" and rows[0]["buckets"] == 2
    rc, rows = run_json(capsys, "list", "--index", "idx", "--earliest", "150", "--latest", "250")
    assert rc == 0 and [(r["bucket"], r["state"]) for r in rows] == [("db_200_100_1", "archived")]


def test_list_text_table(instance, capsys):
    assert cli.main(["list", "--index", "idx"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "index",
        "bucket",
        "earliest",
        "latest",
        "bytes",
        "has_l2hash",
        "state",
    ]
    assert len(lines) == 3


def test_restore_is_a_dry_run_without_yes(instance, capsys):
    rc, rows = run_json(capsys, "restore", "--index", "idx")
    assert rc == cli.EXIT_DRY_RUN
    assert {r["status"] for r in rows} == {"would_restore"}
    assert os.listdir(str(instance)) == []


def test_restore_yes_then_skip(instance, splunk_home, capsys):
    rc, rows = run_json(capsys, "restore", "--index", "idx", "--yes")
    assert rc == 0 and [r["status"] for r in rows] == ["restored", "restored"]
    rc, rows = run_json(capsys, "restore", "--index", "idx", "--yes")
    assert rc == 0 and {r["status"] for r in rows} == {"skipped"}
    log = (splunk_home / "var" / "log" / "splunk" / "archive_restore.log").read_text()
    assert "user=cli:" in log and "status=restored" in log


def test_restore_failure_exit_code(instance, capsys, monkeypatch):
    monkeypatch.setenv("STUB_REBUILD_RC", "1")
    rc, rows = run_json(capsys, "restore", "--index", "idx", "--yes")
    assert rc == cli.EXIT_FAILED and rows[0]["status"] == "failed"


def test_restore_rejected(instance, splunk_home, capsys):
    rc = cli.main(["restore", "--index", "nope", "--yes"])
    assert rc == cli.EXIT_REJECTED and "does not exist" in capsys.readouterr().err
    log = (splunk_home / "var" / "log" / "splunk" / "archive_restore.log").read_text()
    assert "status=rejected" in log


def test_restore_refused_for_other_user(instance, capsys, monkeypatch):
    monkeypatch.setattr(lc.os, "geteuid", lambda: os.getuid() + 1)
    rc, _ = run_json(capsys, "restore", "--index", "idx", "--yes")
    assert rc == cli.EXIT_REJECTED and os.listdir(str(instance)) == []


def test_delete_dry_run_then_yes(instance, capsys):
    run_json(capsys, "restore", "--index", "idx", "--yes")
    rc, rows = run_json(capsys, "delete", "--index", "idx", "--bucket", "*", "--latest", "250")
    assert rc == cli.EXIT_DRY_RUN and [(r["bucket"], r["status"]) for r in rows] == [
        ("db_200_100_1", "would_delete")
    ]
    assert len(os.listdir(str(instance))) == 2
    rc, rows = run_json(capsys, "delete", "--index", "idx", "--bucket", "db_200_100_1", "--yes")
    assert rc == 0 and rows[0]["status"] == "deleted"
    assert os.listdir(str(instance)) == ["db_400_300_2"]


def test_delete_takes_one_index(instance, capsys):
    rc, _ = run_json(capsys, "delete", "--index", "idx,other", "--bucket", "*", "--yes")
    assert rc == cli.EXIT_REJECTED


@pytest.mark.parametrize(
    "value,ok",
    [("1700000000", True), ("2024-01-31", True), ("2024-01-31T10:30", True), ("x", False)],
)
def test_parse_time(value, ok):
    if ok:
        assert cli.parse_time(value) > 0
    else:
        with pytest.raises(cli.argparse.ArgumentTypeError):
            cli.parse_time(value)
