import io
import json
from types import SimpleNamespace

import pytest

from archive_restore import splunk_ctx as ctx


class FakeIndex:
    def __init__(self, content):
        self.content = content


class FakeService:
    def __init__(self, caps=(), indexes=None, confs=None):
        self.caps = list(caps)
        self.indexes = indexes or {}
        self.confs = confs or {}

    def get(self, path, **kw):
        assert path == "authentication/current-context"
        body = json.dumps({"entry": [{"content": {"capabilities": self.caps}}]})
        return SimpleNamespace(body=io.BytesIO(body.encode()))


def test_parse_index_list():
    assert ctx.parse_index_list(" b, a ,a ") == ["a", "b"]
    for bad in ("", " , ", "a,../x", "A"):
        with pytest.raises(ctx.ContextError):
            ctx.parse_index_list(bad)


def test_time_range_defaults(monkeypatch):
    monkeypatch.setattr(ctx.time, "time", lambda: 1000.0)
    assert ctx.time_range(SimpleNamespace(earliest_time="100.5", latest_time="200")) == (100, 200)
    assert ctx.time_range(SimpleNamespace(earliest_time=None, latest_time=0)) == (0, 1000)


def test_require_capability_fails_closed():
    ctx.require_capability(FakeService(caps=["search", "archive_restore"]))
    with pytest.raises(ctx.ContextError):
        ctx.require_capability(FakeService(caps=["search"]))


def test_thawed_dir():
    svc = FakeService(
        indexes={"idx": FakeIndex({"thawedPath_expanded": "/t/idx"}), "nopath": FakeIndex({})}
    )
    assert str(ctx.thawed_dir(svc, "idx")) == "/t/idx"
    for name in ("missing", "nopath"):
        with pytest.raises(ctx.ContextError):
            ctx.thawed_dir(svc, name)


def test_load_settings():
    svc = FakeService(
        confs={
            "archive_restore": {
                "settings": SimpleNamespace(
                    content={"archive_root": "/archive", "max_buckets_per_run": "3"}
                )
            }
        }
    )
    assert ctx.load_settings(svc).max_buckets_per_run == 3
    with pytest.raises(ctx.ContextError):
        ctx.load_settings(FakeService())
