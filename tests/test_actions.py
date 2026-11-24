import pytest

from archive_restore import actions, ops
from archive_restore.config import Settings
from conftest import make_bucket


def thawed_fn(tmp_path, known=("idx", "other")):
    def fn(name):
        if name not in known:
            raise actions.IndexNotFound(name)
        d = tmp_path / "thawed" / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    return fn


def test_list_summary(archive):
    make_bucket(archive, "idx", "db_200_100_1", size=5)
    make_bucket(archive, "idx", "db_400_300_2", size=5)
    make_bucket(archive, "other", "db_50_10_3")
    rows = list(actions.list_summary(archive))
    assert [(r["index"], r["buckets"], r["earliest"], r["latest"]) for r in rows] == [
        ("idx", 2, 100, 400),
        ("other", 1, 10, 50),
    ]


def test_list_buckets_states(archive, tmp_path):
    make_bucket(archive, "idx", "db_200_100_1")
    make_bucket(archive, "idx", "db_400_300_2")
    make_bucket(archive, "idx", "db_900_800_3")  # outside the range
    make_bucket(archive, "gone", "db_200_100_4")
    fn = thawed_fn(tmp_path)
    (fn("idx") / "db_400_300_2").mkdir()
    rows = list(actions.list_buckets(archive, ["idx", "gone"], 150, 350, fn))
    assert [(r["index"], r["bucket"], r["state"]) for r in rows] == [
        ("idx", "db_200_100_1", "archived"),
        ("idx", "db_400_300_2", "thawed"),
        ("gone", "db_200_100_4", "index_missing"),
    ]


def test_plan_and_run_restore(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_1")
    make_bucket(archive, "other", "db_200_100_2")
    settings = Settings(archive_root=archive)
    plans = actions.plan_restore(settings, ["idx", "other"], 0, 1000, thawed_fn(tmp_path))
    rows = list(actions.run_restore(plans, settings, logger, "u"))
    assert [(r["index"], r["status"]) for r in rows] == [("idx", "restored"), ("other", "restored")]


def test_run_restore_preflight_fails_before_any_change(archive, tmp_path, splunk_home, logger):
    make_bucket(archive, "idx", "db_200_100_1")
    make_bucket(archive, "idx", "db_400_300_2")
    settings = Settings(archive_root=archive, max_buckets_per_run=1)
    plans = actions.plan_restore(settings, ["idx"], 0, 1000, thawed_fn(tmp_path))
    with pytest.raises(ops.PreflightError):
        list(actions.run_restore(plans, settings, logger, "u"))
    assert list(plans[0].thawed_dir.iterdir()) == []


def test_plan_restore_unknown_index_raises(archive, tmp_path):
    with pytest.raises(actions.IndexNotFound):
        actions.plan_restore(Settings(archive_root=archive), ["nope"], 0, 1, thawed_fn(tmp_path))


def test_delete_targets(tmp_path):
    thawed = tmp_path / "thaweddb"
    for n in ("db_200_100_1", "db_400_300_2", "db_900_800_3", "junk"):
        (thawed / n).mkdir(parents=True)
    assert actions.delete_targets(thawed, "*", 150, 350) == ["db_200_100_1", "db_400_300_2"]
    assert actions.delete_targets(thawed, "db_900_800_3", 0, 1) == ["db_900_800_3"]
    assert actions.delete_targets(tmp_path / "missing", "*", 0, 1000) == []


def test_run_delete(tmp_path, splunk_home, logger):
    thawed = tmp_path / "thaweddb"
    (thawed / "db_200_100_1").mkdir(parents=True)
    rows = list(actions.run_delete("idx", thawed, ["db_200_100_1"], logger, "u"))
    assert rows[0]["status"] == "deleted" and not (thawed / "db_200_100_1").exists()
