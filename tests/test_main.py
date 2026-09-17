from jobbot import __main__ as cli
from jobbot import auto_apply, extensions, pipeline


def test_scrape_loads_features_before_running(monkeypatch):
    calls = []
    monkeypatch.setattr(extensions, "load_features", lambda: calls.append("load") or [])
    monkeypatch.setattr(pipeline, "main", lambda: calls.append("scrape"))
    cli.main(["scrape"])
    assert calls == ["load", "scrape"]


def test_apply_loads_features_before_running(monkeypatch):
    calls = []
    monkeypatch.setattr(extensions, "load_features", lambda: calls.append("load") or [])
    monkeypatch.setattr(auto_apply, "main", lambda: calls.append("apply"))
    cli.main(["apply"])
    assert calls == ["load", "apply"]
