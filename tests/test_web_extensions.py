import threading
from http.server import ThreadingHTTPServer

import pytest

from jobbot import server

playwright = pytest.importorskip("playwright.sync_api")

FEATURE_JS = """
JobBot.hooks.overviewCards.push(c => { c.insertAdjacentHTML('beforeend', '<div class="card" id="demo-card">Démo</div>'); });
JobBot.hooks.searchExtras.push(c => { c.insertAdjacentHTML('beforeend', '<div class="card" id="demo-search">Réglages démo</div>'); });
JobBot.registerView({ id: 'demo', label: 'Démo', count: () => 3,
  render: s => { s.innerHTML = '<h2 class="view-title">Vue démo</h2><p id="demo-count">' + JobBot.S.offers.length + '</p>'; } });
"""


def test_feature_view_and_hooks_render(tmp_db, monkeypatch, tmp_path):
    feats = tmp_path / "features"
    feats.mkdir()
    (feats / "demo.js").write_text(FEATURE_JS, encoding="utf-8")
    monkeypatch.setattr(server, "FEATURES_WEB_DIR", feats)
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    errors = []
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:
                pytest.skip("navigateur Playwright non installé")
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/#/apercu")
            page.wait_for_selector("#demo-card", timeout=5000)
            assert page.locator('nav.tabs a[data-view="demo"] .count').inner_text() == "3"
            page.goto(f"{base}/#/demo")
            page.wait_for_selector("#view-demo:not([hidden]) #demo-count", timeout=5000)
            page.goto(f"{base}/#/recherche")
            page.wait_for_selector("#demo-search", timeout=5000)
            page.wait_for_timeout(1500)
            assert page.locator("#demo-search").count() == 1  # searchExtras rendu une seule fois
            browser.close()
    finally:
        httpd.shutdown()
    assert errors == []
