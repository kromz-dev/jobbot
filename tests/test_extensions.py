import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jobbot import db, extensions, pipeline, scraper, server
from jobbot.sources import SOURCES


def _serve(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


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


def test_offer_decorators_applied_on_get_and_update_offer(clean_extensions, tmp_db):
    run = db.start_run({})
    db.ingest([{"apply_url": "https://x/1", "source": "Hellowork", "title": "Aide-soignant", "company": "A",
                "location": "Pamiers - 09", "search_hub": "Pamiers"}], run)
    oid = db.list_offers()[0]["id"]

    @extensions.offer_decorator
    def tag(offers):
        for o in offers:
            o["tagged"] = True

    assert db.get_offer(oid)["tagged"] is True
    assert db.update_offer(oid, note="x")["tagged"] is True


def test_migration_rejects_invalid_name(clean_extensions):
    with pytest.raises(ValueError):
        extensions.migration("bad name; DROP TABLE offers", "ALTER TABLE demo ADD COLUMN x TEXT")


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


def test_load_features_skips_broken_module(clean_extensions, tmp_path, monkeypatch):
    import jobbot.features as pkg

    (tmp_path / "zz_good.py").write_text(
        "from jobbot import extensions\n@extensions.route('GET', r'/api/zz-good')\ndef h(req, m, q, b):\n    return 200, {}\n",
        encoding="utf-8",
    )
    (tmp_path / "zz_bad.py").write_text("raise RuntimeError('boum')\n", encoding="utf-8")
    monkeypatch.setattr(pkg, "__path__", [str(tmp_path)])
    loaded = extensions.load_features()
    assert loaded == ["zz_good"]
    assert len(extensions.FAILED_FEATURES) == 1
    name, error = extensions.FAILED_FEATURES[0]
    assert name == "zz_bad"
    assert "boum" in error


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


def test_render_index_injects_feature_scripts():
    html = "<body><script src='/static/app.js'></script>\n<!-- FEATURE_SCRIPTS -->\n</body>"
    out = server.render_index(html, ["b.js", "a.js"])
    assert '<script src="/static/features/a.js"></script>' in out
    assert out.index("features/a.js") < out.index("features/b.js")
    assert "FEATURE_SCRIPTS" not in out


def test_public_config_masks_secrets():
    cfg = {"integrations": {"francetravail": {"client_id": "PAR_abc", "client_secret": "s3cr3t-value-1234"}}}
    pub = server.public_config(cfg)
    assert pub["integrations"]["francetravail"]["client_id"] == "PAR_abc"
    assert pub["integrations"]["francetravail"]["client_secret"] == "••••1234"
    assert cfg["integrations"]["francetravail"]["client_secret"] == "s3cr3t-value-1234"  # original intact


def test_public_config_masks_api_key_but_not_client_id():
    cfg = {"integrations": {"demo": {"client_id": "PAR_abc", "api_key": "s3cr3t-value-1234"}}}
    pub = server.public_config(cfg)
    assert pub["integrations"]["demo"]["client_id"] == "PAR_abc"
    assert pub["integrations"]["demo"]["api_key"] == "••••1234"


def test_public_config_masks_short_secret_fully():
    cfg = {"integrations": {"francetravail": {"client_secret": "short1"}}}
    pub = server.public_config(cfg)
    assert pub["integrations"]["francetravail"]["client_secret"] == server.MASK


def test_save_config_preserves_integrations_and_masked_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    server.save_config({"integrations": {"francetravail": {"client_id": "id1", "client_secret": "real-secret-9999"}}})
    server.save_config({"radius_km": 12})  # le front n'envoie pas integrations
    assert server.load_config()["integrations"]["francetravail"]["client_secret"] == "real-secret-9999"
    server.save_config({"integrations": {"francetravail": {"client_id": "id2", "client_secret": "••••9999"}}})
    saved = server.load_config()["integrations"]["francetravail"]
    assert saved == {"client_id": "id2", "client_secret": "real-secret-9999"}


def test_save_config_ignores_non_dict_integration_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    cfg = server.save_config({"integrations": {"demo": {"client_id": "x"}, "broken": "not-a-dict"}})
    assert cfg["integrations"]["demo"]["client_id"] == "x"
    assert "broken" not in cfg["integrations"] or cfg["integrations"]["broken"] == {}


def test_save_config_serializes_concurrent_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    server.save_config({"radius_km": 1})
    original_write_text = Path.write_text
    active = {"n": 0}
    overlap = []

    def slow_write_text(self, *a, **k):
        active["n"] += 1
        overlap.append(active["n"])
        time.sleep(0.05)
        active["n"] -= 1
        return original_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", slow_write_text)
    threads = [threading.Thread(target=server.save_config, args=({"radius_km": i},)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(overlap) == 1  # jamais deux écritures en même temps : pas de lecture-fusion-écriture concurrente


def test_registered_route_served_over_http(clean_extensions, tmp_db, monkeypatch, tmp_path):
    @extensions.route("POST", r"/api/echo/(\w+)")
    def echo(req, match, query, body):
        return 201, {"name": match.group(1), "body": body}

    httpd, base = _serve(monkeypatch, tmp_path)
    try:
        req = urllib.request.Request(f"{base}/api/echo/abc", data=b'{"x": 1}', method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            assert r.status == 201
            assert json.loads(r.read()) == {"name": "abc", "body": {"x": 1}}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_pipeline_run_never_persists_secrets(clean_extensions, tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "scrape_all", lambda **kw: [])
    monkeypatch.setattr(pipeline, "CSV_PATH", tmp_path / "out.csv")
    monkeypatch.setattr(pipeline, "JSON_PATH", tmp_path / "out.json")
    cfg = {"sources": ["Hellowork"], "cities": ["Paris"], "integrations": {"francetravail": {"client_secret": "s3cr3t-value"}}}
    pipeline.run(cfg)
    runs = db.list_runs()
    assert "integrations" not in runs[0]["config"]
    assert runs[0]["config"]["cities"] == ["Paris"]


def test_dispatch_returns_500_json_for_non_serializable_payload(clean_extensions, tmp_db, monkeypatch, tmp_path):
    @extensions.route("GET", r"/api/broken")
    def broken(req, match, query, body):
        return 200, {"nope": {1, 2, 3}}  # un set n'est pas sérialisable en JSON

    httpd, base = _serve(monkeypatch, tmp_path)
    try:
        req = urllib.request.Request(f"{base}/api/broken")
        try:
            with urllib.request.urlopen(req) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
            body = json.loads(e.read())
            assert "error" in body
        assert status == 500
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_feature_script_served_and_injected(tmp_db, monkeypatch, tmp_path):
    feats = tmp_path / "features"
    feats.mkdir()
    (feats / "demo.js").write_text("window.__demo = 1;", encoding="utf-8")
    monkeypatch.setattr(server, "FEATURES_WEB_DIR", feats)
    httpd, base = _serve(monkeypatch, tmp_path)
    try:
        with urllib.request.urlopen(f"{base}/") as r:
            assert '/static/features/demo.js' in r.read().decode()
        with urllib.request.urlopen(f"{base}/static/features/demo.js") as r:
            assert r.read() == b"window.__demo = 1;"
    finally:
        httpd.shutdown()
        httpd.server_close()
