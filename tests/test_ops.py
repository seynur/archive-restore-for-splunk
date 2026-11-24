import os
import shutil
import threading
import time

import pytest

from archive_restore import buckets as bk
from archive_restore import ops
from archive_restore.config import Settings
from conftest import GUID, make_bucket


def settings(archive, **kw):
    return Settings(archive_root=archive, **kw)


def plan_for(archive, tmp_path, index="idx"):
    thawed = tmp_path / "thaweddb"
    thawed.mkdir(exist_ok=True)
    return ops.IndexPlan(index, thawed, bk.scan_index(archive, index)[0])


def calls(splunk_home):
    path = splunk_home / "calls.log"
    return path.read_text().splitlines() if path.exists() else []


# --- run() -----------------------------------------------------------------


def test_run_argv_no_shell(splunk_home):
    tricky = str(splunk_home / "x; touch pwned")
    rc, out = ops.run([ops.splunk_bin(), "rebuild", tricky, "idx"], timeout=10)
    assert rc == 0 and ("rebuilding " + tricky) in out
    assert not (splunk_home / "pwned").exists() and not os.path.exists("pwned")


def test_run_timeout_kills_whole_group(splunk_home, monkeypatch):
    # The stub's `sleep` is a grandchild holding stdout; killing only the direct child
    # would make communicate() wait the full 30 s.
    monkeypatch.setenv("STUB_SLEEP", "30")
    start = time.time()
    rc, _ = ops.run([ops.splunk_bin(), "rebuild", "/x", "idx"], timeout=1)
    assert rc == ops.TIMEOUT_RC and time.time() - start < 10


def test_run_caps_output(tmp_path):
    rc, out = ops.run(["/bin/sh", "-c", "yes | head -c 5000"], timeout=10, max_output=100)
    assert rc == 0 and out.endswith("[truncated]") and len(out) < 200


# --- restore ---------------------------------------------------------------


def test_restore_happy_path(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_7_" + GUID)
    make_bucket(archive, "idx", "db_400_300_8", l2hash=False)
    plan = plan_for(archive, tmp_path)
    rows = list(ops.restore_buckets(plan, settings(archive), logger, "alice"))
    assert [r["status"] for r in rows] == ["restored", "restored"]
    assert sorted(os.listdir(plan.thawed_dir)) == ["db_200_100_7_" + GUID, "db_400_300_8"]
    log = calls(splunk_home)
    assert sum(c.startswith("check-integrity") for c in log) == 1  # only bucket with l2hash
    assert sum(c.startswith("rebuild") for c in log) == 2
    assert all(c.endswith(" idx") for c in log if c.startswith("rebuild"))
    # archive untouched
    assert (archive / "idx" / "db_400_300_8" / "rawdata" / "journal.zst").exists()


def test_restore_is_idempotent(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_7")
    plan = plan_for(archive, tmp_path)
    list(ops.restore_buckets(plan, settings(archive), logger, "alice"))
    rows = list(ops.restore_buckets(plan, settings(archive), logger, "alice"))
    assert rows[0]["status"] == "skipped"
    assert sum(c.startswith("rebuild") for c in calls(splunk_home)) == 1


def test_dry_run_changes_nothing(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_7")
    plan = plan_for(archive, tmp_path)
    rows = list(ops.restore_buckets(plan, settings(archive), logger, "alice", dry_run=True))
    assert rows[0]["status"] == "would_restore"
    assert os.listdir(plan.thawed_dir) == [] and calls(splunk_home) == []


def test_failed_rebuild_cleans_staging_and_continues(
    archive, tmp_path, splunk_home, logger, monkeypatch
):
    make_bucket(archive, "idx", "db_200_100_7")
    make_bucket(archive, "idx", "db_400_300_8")
    plan = plan_for(archive, tmp_path)
    monkeypatch.setenv("STUB_REBUILD_RC", "3")
    rows = list(ops.restore_buckets(plan, settings(archive), logger, "alice"))
    assert [r["status"] for r in rows] == ["failed", "failed"]
    assert "rebuild failed (rc=3)" in rows[0]["reason"]
    assert os.listdir(plan.thawed_dir) == []  # no bucket, no staging dir left


def test_failed_integrity_skips_rebuild(archive, tmp_path, splunk_home, logger, monkeypatch):
    make_bucket(archive, "idx", "db_200_100_7")
    monkeypatch.setenv("STUB_INTEGRITY_OUT", "succeeded=0, failed=1")
    rows = list(ops.restore_buckets(plan_for(archive, tmp_path), settings(archive), logger, "a"))
    assert rows[0]["status"] == "failed" and "integrity" in rows[0]["reason"]
    assert not any(c.startswith("rebuild") for c in calls(splunk_home))


def test_integrity_disabled(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_7")
    plan = plan_for(archive, tmp_path)
    list(ops.restore_buckets(plan, settings(archive, check_integrity=False), logger, "a"))
    assert not any(c.startswith("check-integrity") for c in calls(splunk_home))


def test_symlink_inside_bucket_fails(archive, tmp_path, splunk_home, logger):
    b = make_bucket(archive, "idx", "db_200_100_7")
    os.symlink("/etc/passwd", str(b / "rawdata" / "evil"))
    rows = list(ops.restore_buckets(plan_for(archive, tmp_path), settings(archive), logger, "a"))
    assert rows[0]["status"] == "failed" and "symlink" in rows[0]["reason"]


def test_restore_logs_kv_with_user(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_7")
    list(ops.restore_buckets(plan_for(archive, tmp_path), settings(archive), logger, "alice"))
    text = (splunk_home / "var" / "log" / "splunk" / "archive_restore.log").read_text()
    assert "user=alice action=restore index=idx bucket=db_200_100_7 status=restored" in text


# --- preflight -------------------------------------------------------------


def test_preflight_caps(archive, tmp_path):
    for i in range(3):
        make_bucket(archive, "idx", "db_%d_%d_%d" % (i * 10 + 5, i * 10, i))
    plan = plan_for(archive, tmp_path)
    with pytest.raises(ops.PreflightError, match="max_buckets_per_run"):
        ops.preflight([plan], settings(archive, max_buckets_per_run=2))
    with pytest.raises(ops.PreflightError, match="max_bytes_per_run"):
        ops.preflight([plan], settings(archive, max_bytes_per_run=5))
    ops.preflight([plan], settings(archive))


def test_preflight_space(archive, tmp_path, monkeypatch):
    make_bucket(archive, "idx", "db_200_100_7", size=1000)
    plan = plan_for(archive, tmp_path)
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(0, 0, 100))
    with pytest.raises(ops.PreflightError, match="free"):
        ops.preflight([plan], settings(archive))


def test_preflight_missing_thaweddb(archive, tmp_path):
    plan = ops.IndexPlan("idx", tmp_path / "missing", [])
    with pytest.raises(ops.PreflightError, match="does not exist"):
        ops.preflight([plan], settings(archive))


# --- lock ------------------------------------------------------------------


def test_lock_is_exclusive(tmp_path):
    path = tmp_path / "run" / "x.lock"
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with ops.exclusive_lock(path):
            entered.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    entered.wait(5)
    try:
        with pytest.raises(ops.RestoreBusy):
            with ops.exclusive_lock(path):
                pass
    finally:
        release.set()
        t.join()
    with ops.exclusive_lock(path):
        pass


# --- delete ----------------------------------------------------------------


def test_delete_thawed(tmp_path, splunk_home, logger):
    thawed = tmp_path / "thaweddb"
    (thawed / "db_200_100_7" / "rawdata").mkdir(parents=True)
    os.symlink("/etc", str(thawed / "db_300_250_1"))
    rows = list(
        ops.delete_thawed(
            "idx", thawed, ["db_200_100_7", "db_9_8_7", "../../etc", "db_300_250_1"], logger, "bob"
        )
    )
    assert [r["status"] for r in rows] == ["deleted", "not_found", "failed", "failed"]
    assert os.path.islink(str(thawed / "db_300_250_1"))  # symlink never followed/removed
    assert not (thawed / "db_200_100_7").exists()


def test_rows_share_one_schema(archive, tmp_path, splunk_home, logger, monkeypatch):
    # splunklib takes the output fields from the first row; later fields would be dropped.
    make_bucket(archive, "idx", "db_200_100_7")
    plan = plan_for(archive, tmp_path)
    ok = list(ops.restore_buckets(plan, settings(archive), logger, "a"))
    monkeypatch.setenv("STUB_REBUILD_RC", "1")
    make_bucket(archive, "idx", "db_400_300_8")
    plan = plan_for(archive, tmp_path)
    mixed = list(ops.restore_buckets(plan, settings(archive), logger, "a"))
    deleted = list(ops.delete_thawed("idx", plan.thawed_dir, ["db_200_100_7"], logger, "a"))
    assert {tuple(r) for r in ok + mixed + deleted} == {ops.ROW_FIELDS}
    with pytest.raises(KeyError):
        ops.result_row("restore", "idx", "b", "x", surprise=1)


def test_leftover_staging_from_killed_run_is_replaced(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_7")
    plan = plan_for(archive, tmp_path)
    stale = plan.thawed_dir / ops.STAGING_DIR / "db_200_100_7" / "db_200_100_7"
    stale.mkdir(parents=True)
    (stale / "half-copied").write_text("x")
    rows = list(ops.restore_buckets(plan, settings(archive), logger, "a"))
    assert rows[0]["status"] == "restored"
    assert os.listdir(plan.thawed_dir) == ["db_200_100_7"]
    assert not (plan.thawed_dir / "db_200_100_7" / "half-copied").exists()


def test_preflight_caps_ignore_already_thawed(archive, tmp_path):
    make_bucket(archive, "idx", "db_200_100_1")
    make_bucket(archive, "idx", "db_400_300_2")
    plan = plan_for(archive, tmp_path)
    (plan.thawed_dir / "db_200_100_1").mkdir()
    ops.preflight([plan], settings(archive, max_buckets_per_run=1))


def test_symlinked_staging_root_is_refused(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_7")
    plan = plan_for(archive, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "db_200_100_7").mkdir(parents=True)
    os.symlink(str(elsewhere), str(plan.thawed_dir / ops.STAGING_DIR))
    rows = list(ops.restore_buckets(plan, settings(archive), logger, "a"))
    assert rows[0]["status"] == "failed" and "symlink" in rows[0]["reason"]
    assert (elsewhere / "db_200_100_7").is_dir()  # nothing deleted through the link
