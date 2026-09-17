import json

from jobbot import db, extensions, pipeline, scraper
from jobbot.sources import SOURCES


def test_route_registration_and_lookup(clean_extensions):
    @extensions.route("GET", r"/api/demo/(\d+)")
    def demo(req, match, query, body):
        return 200, {"id": int(match.group(1))}

    found = extensions.find_route("GET", "/api/demo/42")
    assert found is not None
    route, match = found
    assert route.handler(None, match, {}, {}) == (200, {"id": 42})
    assert extensions.find_route("POST", "/api/demo/42") is None
    assert extensions.find_route("GET", "/api/demo/42/x") is None


def test_schema_and_migration_applied_once(clean_extensions, tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    extensions.schema("CREATE TABLE IF NOT EXISTS demo (id INTEGER PRIMARY KEY, label TEXT)")
    extensions.migration("demo_add_color", "ALTER TABLE demo ADD COLUMN color TEXT DEFAULT ''")
    db.init()
    db.init()  # idempotent : la migration ne doit pas être rejouée
    with db.connect() as c:
        cols = [r["name"] for r in c.execute("PRAGMA table_info(demo)")]
        applied = [r["name"] for r in c.execute("SELECT name FROM migrations")]
    assert cols == ["id", "label", "color"]
    assert applied == ["demo_add_color"]
    db._reset(path)


def test_offer_decorators_run_in_list_offers(clean_extensions, tmp_db):
    run = db.start_run({})
    db.ingest([{"apply_url": "https://x/1", "source": "Hellowork", "title": "Aide-soignant", "company": "A",
                "location": "Pamiers - 09", "search_hub": "Pamiers"}], run)

    @extensions.offer_decorator
    def tag(offers):
        for o in offers:
            o["tagged"] = True

    assert all(o["tagged"] for o in db.list_offers())


def test_all_sources_is_dynamic_and_integrations_kept(monkeypatch):
    monkeypatch.setitem(SOURCES, "Demo", lambda *a, **k: None)
    assert "Demo" in scraper.all_sources()
    cfg = scraper.normalize_config({"sources": ["Demo"], "integrations": {"francetravail": {"client_id": "abc", "x": 3}}})
    assert cfg["sources"] == ["Demo"]
    assert cfg["integrations"] == {"francetravail": {"client_id": "abc", "x": "3"}}
    assert scraper.normalize_config({})["integrations"] == {}


def test_post_run_hooks_isolate_errors(clean_extensions):
    calls, logs = [], []

    @extensions.post_run
    def broken(cfg, summary, log):
        raise RuntimeError("boum")

    @extensions.post_run
    def ok(cfg, summary, log):
        calls.append(summary["run_id"])

    pipeline.run_post_hooks({}, {"run_id": 7}, logs.append)
    assert calls == [7]
    assert any("broken" in line and "boum" in line for line in logs)


def test_load_features_imports_modules(clean_extensions, tmp_path, monkeypatch):
    import jobbot.features as pkg

    (tmp_path / "zz_demo_feature.py").write_text(
        "from jobbot import extensions\n"
        "@extensions.route('GET', r'/api/zz-demo')\n"
        "def h(req, m, q, b):\n    return 200, {'ok': True}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pkg, "__path__", [str(tmp_path)])
    assert "zz_demo_feature" in extensions.load_features()
    assert extensions.find_route("GET", "/api/zz-demo") is not None
    json.dumps(extensions.REGISTRIES)  # REGISTRIES est une liste de noms sérialisable
