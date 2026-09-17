# Points d'extension — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettre à chaque nouvelle fonctionnalité (plans 01 à 05) de vivre dans ses propres fichiers (`jobbot/features/*.py`, `jobbot/web/features/*.js`) sans modifier le cœur, pour que chaque plan se développe sur sa branche sans conflit de fusion.

**Architecture:** Un module `jobbot/extensions.py` expose des registres (routes HTTP, schémas SQL, migrations, crochets post-recherche, décorateurs d'offres). Le serveur charge automatiquement `jobbot/features/*`, route les requêtes inconnues vers le registre, et injecte `jobbot/web/features/*.js` dans la page. Côté navigateur, `window.JobBot` expose l'état, les utilitaires, `registerView()` et des crochets (`detailExtras`, `overviewCards`, `searchExtras`, `afterLoad`).

**Tech Stack:** Python 3.10+ (stdlib http.server, sqlite3), pytest, JavaScript vanilla, Playwright (test de fumée optionnel).

## Global Constraints

- Branche : `feat/points-extension`, créée depuis `main`, **fusionnée dans `main` avant de créer les branches des plans 01 à 05**.
- Aucune nouvelle dépendance Python obligatoire.
- Les tests ne touchent jamais `data/` : bases et configs en `tmp_path`.
- `ruff check .` et `pytest -q` doivent passer à la fin de chaque tâche.
- Textes d'interface en français ; aucun emoji comme icône.
- Les registres sont des listes module-level ; les tests les restaurent (fixture `clean_extensions`).

## File Structure

| Fichier | Rôle |
|---|---|
| `jobbot/extensions.py` (créé) | Registres + `load_features()` |
| `jobbot/features/__init__.py` (créé) | Paquet des fonctionnalités (chargé automatiquement) |
| `jobbot/web/features/.gitkeep` (créé) | Dossier des scripts front des fonctionnalités |
| `jobbot/db.py` (modifié) | Applique schémas/migrations enregistrés ; décorateurs d'offres |
| `jobbot/scraper.py` (modifié) | `all_sources()` dynamique ; `integrations` conservé dans la config |
| `jobbot/pipeline.py` (modifié) | `run_post_hooks()` après chaque recherche |
| `jobbot/server.py` (modifié) | Routes enregistrées, injection des scripts, config sans secrets |
| `jobbot/web/app.js` (modifié) | `window.JobBot`, `registerView`, crochets |
| `jobbot/web/index.html` (modifié) | Emplacements `overview-extras`, `search-extras`, `<!-- FEATURE_SCRIPTS -->` |
| `tests/conftest.py` (créé) | Fixtures `tmp_db`, `clean_extensions` |
| `tests/test_extensions.py` (créé) | Tests backend |
| `tests/test_web_extensions.py` (créé) | Test de fumée navigateur (sauté si Playwright absent) |

---

### Task 1: Registres backend (routes, schémas, migrations, crochets, décorateurs)

**Files:**
- Create: `jobbot/extensions.py`
- Create: `jobbot/features/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_extensions.py`
- Modify: `jobbot/db.py` (fonctions `init` et `list_offers`, constante `SCHEMA`)
- Modify: `jobbot/scraper.py` (`ALL_SOURCES`, `DEFAULT_CONFIG`, `normalize_config`)
- Modify: `jobbot/pipeline.py` (fin de `run`)

**Interfaces:**
- Consumes: `db.connect`, `db.init`, `db.list_offers`, `scraper.SOURCES` (existants)
- Produces:
  - `extensions.route(method: str, pattern: str)` → décorateur ; handler `(req, match: re.Match, query: dict[str, list[str]], body: dict) -> tuple[int, object]`
  - `extensions.find_route(method: str, path: str) -> tuple[Route, re.Match] | None`
  - `extensions.schema(sql: str) -> None` ; `extensions.migration(name: str, sql: str) -> None`
  - `extensions.post_run(fn)` avec `fn(cfg: dict, summary: dict, log: Callable[[str], None]) -> None`
  - `extensions.offer_decorator(fn)` avec `fn(offers: list[dict]) -> None` (modifie la liste en place)
  - `extensions.load_features() -> list[str]`
  - `scraper.all_sources() -> list[str]`
  - config : clé `integrations: dict[str, dict[str, str]]` conservée par `normalize_config`
  - `pipeline.run_post_hooks(cfg: dict, summary: dict, log) -> None`

- [ ] **Step 1: Écrire les fixtures de test**

`tests/conftest.py` :

```python
import pytest

from jobbot import db, extensions


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Base SQLite isolée : db.DB_PATH pointe vers tmp_path."""
    path = tmp_path / "jobbot.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db._reset(path)
    db.init()
    yield path
    db._reset(path)


@pytest.fixture
def clean_extensions():
    """Restaure les registres après le test."""
    saved = {name: list(getattr(extensions, name)) for name in extensions.REGISTRIES}
    yield extensions
    for name, values in saved.items():
        getattr(extensions, name)[:] = values
```

- [ ] **Step 2: Écrire les tests qui échouent**

`tests/test_extensions.py` :

```python
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
```

- [ ] **Step 3: Lancer les tests pour vérifier l'échec**

Run: `python3 -m pytest tests/test_extensions.py -q`
Expected: FAIL — `ImportError: cannot import name 'extensions' from 'jobbot'`

- [ ] **Step 4: Créer `jobbot/extensions.py` et le paquet features**

`jobbot/extensions.py` :

```python
"""
Points d'extension. Une fonctionnalité (jobbot/features/<nom>.py) s'y enregistre à l'import :

    from jobbot import extensions

    @extensions.route("GET", r"/api/prospects")
    def list_prospects(req, match, query, body):
        return 200, [...]

Le serveur charge automatiquement toutes les fonctionnalités au démarrage (load_features).
Ce module ne doit importer aucun autre module de jobbot (évite les imports circulaires).
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern
    handler: Callable


ROUTES: list[Route] = []
SCHEMAS: list[str] = []
MIGRATIONS: list[tuple[str, str]] = []
POST_RUN_HOOKS: list[Callable] = []
OFFER_DECORATORS: list[Callable[[list[dict]], None]] = []
REGISTRIES = ["ROUTES", "SCHEMAS", "MIGRATIONS", "POST_RUN_HOOKS", "OFFER_DECORATORS"]


def route(method: str, pattern: str):
    """Enregistre un handler HTTP : handler(req, match, query, body) -> (status, payload JSON)."""
    def deco(fn: Callable) -> Callable:
        ROUTES.append(Route(method.upper(), re.compile(pattern), fn))
        return fn
    return deco


def find_route(method: str, path: str) -> tuple[Route, re.Match] | None:
    for r in ROUTES:
        if r.method == method.upper():
            match = r.pattern.fullmatch(path)
            if match:
                return r, match
    return None


def schema(sql: str) -> None:
    """SQL idempotent (CREATE TABLE IF NOT EXISTS …) exécuté à chaque db.init()."""
    if sql not in SCHEMAS:
        SCHEMAS.append(sql)


def migration(name: str, sql: str) -> None:
    """SQL non idempotent (ALTER TABLE …) exécuté une seule fois, mémorisé dans la table migrations."""
    if name not in {n for n, _ in MIGRATIONS}:
        MIGRATIONS.append((name, sql))


def post_run(fn: Callable) -> Callable:
    """fn(cfg, summary, log) appelé à la fin de chaque recherche réussie ou arrêtée."""
    POST_RUN_HOOKS.append(fn)
    return fn


def offer_decorator(fn: Callable[[list[dict]], None]) -> Callable[[list[dict]], None]:
    """fn(offers) enrichit en place la liste renvoyée par db.list_offers()."""
    OFFER_DECORATORS.append(fn)
    return fn


def load_features() -> list[str]:
    """Importe chaque module de jobbot.features (hors noms commençant par « _ »)."""
    import jobbot.features as pkg

    names = []
    for mod in pkgutil.iter_modules(pkg.__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"jobbot.features.{mod.name}")
            names.append(mod.name)
    return sorted(names)
```

`jobbot/features/__init__.py` :

```python
"""Fonctionnalités optionnelles, chargées automatiquement par extensions.load_features()."""
```

- [ ] **Step 5: Brancher les schémas, migrations et décorateurs dans `jobbot/db.py`**

Ajouter l'import en tête (après `from . import intel`) :

```python
from . import extensions, intel
```

(et supprimer l'ancienne ligne `from . import intel`).

Ajouter à la fin de la constante `SCHEMA`, juste avant les trois guillemets fermants :

```sql

CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied_at TEXT);
```

Remplacer la fonction `init` :

```python
@retry_io
def init(path: Path | str | None = None) -> None:
    with _write_lock, connect(path) as c:
        c.executescript(SCHEMA)
        for sql in extensions.SCHEMAS:
            c.executescript(sql)
        applied = {r["name"] for r in c.execute("SELECT name FROM migrations")}
        for name, sql in extensions.MIGRATIONS:
            if name not in applied:
                c.executescript(sql)
                c.execute("INSERT INTO migrations (name, applied_at) VALUES (?, ?)", (name, now_iso()))
```

Dans `list_offers`, remplacer la dernière ligne `return [_offer_dict(o, listings[o["id"]], history[o["id"]]) for o in offers]` par :

```python
    result = [_offer_dict(o, listings[o["id"]], history[o["id"]]) for o in offers]
    for decorate in extensions.OFFER_DECORATORS:
        decorate(result)
    return result
```

- [ ] **Step 6: Rendre les sources dynamiques et conserver `integrations` dans `jobbot/scraper.py`**

Remplacer `ALL_SOURCES = ["Indeed", *SOURCES.keys()]` par :

```python
def all_sources() -> list[str]:
    """Sources disponibles, y compris celles ajoutées par une fonctionnalité."""
    return ["Indeed", *SOURCES.keys()]


ALL_SOURCES = all_sources()  # compatibilité : valeur au démarrage
```

Dans `DEFAULT_CONFIG`, ajouter après `"max_enrich": 200,` :

```python
    "integrations": {},       # identifiants d'API optionnels, ex. {"francetravail": {"client_id": "…"}}
```

Dans `normalize_config`, remplacer la ligne `cfg["sources"] = [src for src in cfg["sources"] if src in ALL_SOURCES]` par :

```python
    available = all_sources()
    if "sources" not in raw:
        cfg["sources"] = available
    cfg["sources"] = [src for src in cfg["sources"] if src in available]
```

et ajouter juste avant `return cfg` :

```python
    integrations = raw.get("integrations")
    cfg["integrations"] = {
        str(name): {str(k): str(v)[:500] for k, v in values.items()}
        for name, values in (integrations.items() if isinstance(integrations, dict) else [])
        if isinstance(values, dict)
    }
```

- [ ] **Step 7: Ajouter les crochets post-recherche dans `jobbot/pipeline.py`**

Ajouter l'import `from . import db, extensions` (remplace `from . import db`).

Ajouter la fonction après `LogFn = Callable[[str], None]` :

```python
def run_post_hooks(cfg: dict, summary: dict, log: LogFn) -> None:
    """Appelle chaque fonctionnalité branchée ; une erreur n'interrompt ni la recherche ni les autres crochets."""
    for hook in extensions.POST_RUN_HOOKS:
        try:
            hook(cfg, summary, log)
        except Exception as e:  # noqa: BLE001 — une extension ne doit jamais casser la recherche
            log(f"Extension {hook.__name__}: {str(e)[:200]}")
```

Dans `run`, juste après la ligne `summary["status"] = status`, ajouter :

```python
        run_post_hooks(cfg, summary, log)
```

- [ ] **Step 8: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: tous les tests passent (18 existants + 6 nouveaux).

- [ ] **Step 9: Lint et commit**

```bash
ruff check . --fix && ruff check .
git add jobbot/extensions.py jobbot/features/__init__.py jobbot/db.py jobbot/scraper.py jobbot/pipeline.py tests/conftest.py tests/test_extensions.py
git commit -m "feat(extensions): registres de routes, schémas, migrations, crochets et décorateurs d'offres"
```

---

### Task 2: Serveur — routes enregistrées, scripts de fonctionnalités, config sans secrets

**Files:**
- Modify: `jobbot/server.py`
- Modify: `jobbot/web/index.html`
- Create: `jobbot/web/features/.gitkeep`
- Test: `tests/test_extensions.py` (ajouts)

**Interfaces:**
- Consumes: `extensions.find_route`, `extensions.load_features`, `scraper.all_sources`
- Produces:
  - `server.FEATURES_WEB_DIR: Path` (= `WEB / "features"`, remplaçable en test)
  - `server.render_index(html: str, scripts: list[str]) -> str`
  - `server.public_config(cfg: dict) -> dict` (masque toute valeur d'`integrations` dont la clé contient `secret`, `token` ou `password` par `"••••"` + 4 derniers caractères)
  - `server.save_config(raw)` conserve `integrations` si absent de `raw`, et ne remplace pas un secret masqué (`"••••…"`) par le masque
  - `GET /static/features/<nom>.js` servi depuis `FEATURES_WEB_DIR`

- [ ] **Step 1: Écrire les tests qui échouent** (ajouter à `tests/test_extensions.py`)

```python
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from jobbot import server


def _serve(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


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


def test_save_config_preserves_integrations_and_masked_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    server.save_config({"integrations": {"francetravail": {"client_id": "id1", "client_secret": "real-secret-9999"}}})
    server.save_config({"radius_km": 12})  # le front n'envoie pas integrations
    assert server.load_config()["integrations"]["francetravail"]["client_secret"] == "real-secret-9999"
    server.save_config({"integrations": {"francetravail": {"client_id": "id2", "client_secret": "••••9999"}}})
    saved = server.load_config()["integrations"]["francetravail"]
    assert saved == {"client_id": "id2", "client_secret": "real-secret-9999"}


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
```

- [ ] **Step 2: Lancer les tests pour vérifier l'échec**

Run: `python3 -m pytest tests/test_extensions.py -q`
Expected: FAIL — `AttributeError: module 'jobbot.server' has no attribute 'render_index'`

- [ ] **Step 3: Modifier `jobbot/server.py`**

1. Imports : remplacer `from . import db, pipeline` par `from . import db, extensions, pipeline` et remplacer `from .scraper import ALL_SOURCES, DEFAULT_CONFIG, normalize_config` par `from .scraper import DEFAULT_CONFIG, all_sources, normalize_config`.

2. Après `STATIC_TYPES = …`, ajouter :

```python
FEATURES_WEB_DIR = WEB / "features"
SECRET_KEYS = ("secret", "token", "password")
MASK = "••••"


def render_index(html: str, scripts: list[str]) -> str:
    tags = "\n".join(f'<script src="/static/features/{name}"></script>' for name in sorted(scripts))
    return html.replace("<!-- FEATURE_SCRIPTS -->", tags)


def _is_secret(key: str) -> bool:
    return any(s in key.lower() for s in SECRET_KEYS)


def public_config(cfg: dict) -> dict:
    """Copie de la config sans exposer les secrets au navigateur."""
    out = {**cfg, "integrations": {}}
    for name, values in (cfg.get("integrations") or {}).items():
        out["integrations"][name] = {
            k: (MASK + v[-4:] if _is_secret(k) and v else v) for k, v in values.items()
        }
    return out
```

3. Remplacer `save_config` :

```python
def save_config(raw: dict) -> dict:
    previous = load_config().get("integrations", {})
    raw = dict(raw)
    if "integrations" not in raw:
        raw["integrations"] = previous
    else:
        merged = {}
        for name, values in (raw.get("integrations") or {}).items():
            old = previous.get(name, {})
            merged[name] = {k: (old.get(k, "") if str(v).startswith(MASK) else v) for k, v in values.items()}
        raw["integrations"] = {**previous, **merged}
    cfg = normalize_config(raw)
    cfg["schedule"] = {**SCHEDULE_DEFAULT, **(raw.get("schedule") or {})}
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_config()
```

4. Dans `do_GET`, remplacer le bloc `if path in ("/", "/index.html"):` … jusqu'à la fin du bloc `elif path.startswith("/static/"):` par :

```python
        if path in ("/", "/index.html"):
            scripts = [p.name for p in FEATURES_WEB_DIR.glob("*.js")] if FEATURES_WEB_DIR.exists() else []
            html = render_index((WEB / "index.html").read_text(encoding="utf-8"), scripts)
            self._send(200, html.encode("utf-8"), STATIC_TYPES[".html"])
        elif path.startswith("/static/features/"):
            target = (FEATURES_WEB_DIR / path.removeprefix("/static/features/")).resolve()
            if FEATURES_WEB_DIR.resolve() not in target.parents or target.suffix != ".js":
                self._json(404, {"error": "introuvable"})
            else:
                self._file(target, STATIC_TYPES[".js"])
        elif path.startswith("/static/"):
            target = (WEB / path.removeprefix("/static/")).resolve()
            if WEB.resolve() not in target.parents or target.suffix not in STATIC_TYPES:
                self._json(404, {"error": "introuvable"})
            else:
                self._file(target, STATIC_TYPES[target.suffix])
```

5. Dans `do_GET`, remplacer le bloc `/api/config` par :

```python
        elif path == "/api/config":
            self._json(200, {"config": public_config(load_config()),
                             "defaults": {**DEFAULT_CONFIG, "sources": all_sources(), "schedule": SCHEDULE_DEFAULT},
                             "all_sources": all_sources()})
```

6. Dans `do_GET`, remplacer le `else:` final (404) par :

```python
        else:
            self._dispatch("GET", path, query, {})
```

7. Dans `do_POST`, remplacer la réponse de `/api/config` par `self._json(200, {"ok": True, "config": public_config(save_config(body.get("config") or {}))})` et le `else:` final par :

```python
        else:
            self._dispatch("POST", path, {}, body)
```

8. Ajouter la méthode dans `Handler` (après `_file`) :

```python
    def _dispatch(self, method: str, path: str, query: dict, body: dict) -> None:
        found = extensions.find_route(method, path)
        if not found:
            self._json(404, {"error": "introuvable"})
            return
        route, match = found
        try:
            status, payload = route.handler(self, match, query, body)
        except Exception as e:  # noqa: BLE001 — remonté proprement au navigateur
            status, payload = 500, {"error": f"{route.handler.__name__}: {str(e)[:300]}"}
        self._json(status, payload)
```

9. Dans `boot()`, en première ligne :

```python
    features = extensions.load_features()
    if features:
        print(f"Fonctionnalités : {', '.join(features)}")
```

- [ ] **Step 4: Emplacements dans `jobbot/web/index.html`**

Remplacer `<script src="/static/app.js"></script>` par :

```html
<script src="/static/app.js"></script>
<!-- FEATURE_SCRIPTS -->
```

Juste avant la balise `</section>` qui ferme `view-apercu` (après le `</div>` du second `<div class="grid-2">`), ajouter :

```html
    <div class="grid-2" id="overview-extras" style="margin-top:12px"></div>
```

Dans `view-recherche`, remplacer `<div style="display:grid;gap:12px">` (colonne de droite) par :

```html
      <div style="display:grid;gap:12px">
        <div id="search-extras" style="display:grid;gap:12px"></div>
```

Créer le fichier vide `jobbot/web/features/.gitkeep`.

- [ ] **Step 5: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS (tous).

- [ ] **Step 6: Lint et commit**

```bash
ruff check . && git add jobbot/server.py jobbot/web/index.html jobbot/web/features/.gitkeep tests/test_extensions.py
git commit -m "feat(server): routes des fonctionnalités, injection des scripts, config sans secrets"
```

---

### Task 3: API front `window.JobBot` (vues, crochets)

**Files:**
- Modify: `jobbot/web/app.js`
- Create: `tests/test_web_extensions.py`

**Interfaces:**
- Consumes: emplacements `#overview-extras`, `#search-extras`, script injecté (Task 2)
- Produces (navigateur) :
  - `window.JobBot.registerView({ id, label, render(section), bind?(section), count?() -> number })`
  - `window.JobBot.hooks.detailExtras: Array<(offer) => string>` — HTML ajouté sous la note dans le détail d'une offre
  - `window.JobBot.hooks.overviewCards: Array<(container: HTMLElement) => void>` — appelé à chaque rendu de la vue d'ensemble, `container` vidé avant
  - `window.JobBot.hooks.searchExtras: Array<(container: HTMLElement) => void>` — appelé **une seule fois** au premier rendu de Recherche
  - `window.JobBot.hooks.afterLoad: Array<() => Promise<void> | void>` — après chaque chargement de données
  - `window.JobBot.{ S, api, go, esc, icon, fold, fmtInt, km, dateFr, toast, statusLabel, refreshData, renderView }`

- [ ] **Step 1: Écrire le test de fumée navigateur (échoue)**

`tests/test_web_extensions.py` :

```python
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
```

- [ ] **Step 2: Lancer le test pour vérifier l'échec**

Run: `python3 -m pytest tests/test_web_extensions.py -q`
Expected: FAIL (erreur `JobBot is not defined` ou timeout sur `#demo-card`) — ou SKIP si Playwright n'est pas installé (dans ce cas vérifier manuellement à l'étape 5).

- [ ] **Step 3: Modifier `jobbot/web/app.js`**

1. Remplacer `const VIEWS = ["apercu", "offres", "candidatures", "employeurs", "doublons", "recherche", "historique"];` par :

```javascript
const VIEWS = ["apercu", "offres", "candidatures", "employeurs", "doublons", "recherche", "historique"];
const VIEW_RENDERERS = {};
const EXTRA_VIEWS = [];
const HOOKS = { detailExtras: [], overviewCards: [], searchExtras: [], afterLoad: [] };
let searchExtrasRendered = false;
```

2. Dans `renderOverview`, après la ligne `$("health-mini").innerHTML = healthTable(true);`, ajouter :

```javascript
  const extras = $("overview-extras");
  if (extras) { extras.innerHTML = ""; HOOKS.overviewCards.forEach(fn => fn(extras)); }
```

3. Dans `detailRow`, remplacer la ligne :

```javascript
      <textarea id="note-${o.id}" class="note" data-id="${o.id}" placeholder="Contact, date d'appel, questions…">${esc(o.note)}</textarea>
```

par :

```javascript
      <textarea id="note-${o.id}" class="note" data-id="${o.id}" placeholder="Contact, date d'appel, questions…">${esc(o.note)}</textarea>
      ${HOOKS.detailExtras.map(fn => fn(o)).join("")}
```

4. Dans `renderSearch`, après `if (S.config && !$("cfg-sources").children.length) fillForm(S.config);`, ajouter :

```javascript
  if (!searchExtrasRendered && $("search-extras")) { searchExtrasRendered = true; HOOKS.searchExtras.forEach(fn => fn($("search-extras"))); }
```

5. Remplacer la fonction `renderView` et `refreshData` par :

```javascript
function renderView() {
  renderHeader();
  const core = { apercu: renderOverview, offres: renderOffers, candidatures: renderPipeline, employeurs: renderEmployers,
    doublons: renderDuplicates, recherche: renderSearch, historique: renderHistory };
  if (core[S.view]) core[S.view]();
  else if (VIEW_RENDERERS[S.view]) VIEW_RENDERERS[S.view]($(`view-${S.view}`));
}
async function refreshData() {
  await loadData();
  for (const fn of HOOKS.afterLoad) await fn();
  S.version = S.data.data_version;
  renderView();
}
```

6. Dans `renderHeader`, avant la dernière ligne (`$("btn-notif").classList.toggle(…)`), ajouter :

```javascript
  for (const v of EXTRA_VIEWS) {
    const badge = document.querySelector(`nav.tabs a[data-view="${v.id}"] .count`);
    if (badge && v.count) badge.textContent = fmtInt(v.count());
  }
```

7. Juste avant `/* ======================= Boucle ======================= */`, ajouter :

```javascript
/* ======================= API des fonctionnalités ======================= */
window.JobBot = {
  S, api, go, esc, icon, fold, fmtInt, km, dateFr, toast, statusLabel,
  refreshData: () => refreshData(), renderView: () => renderView(),
  hooks: HOOKS,
  registerView({ id, label, render, bind, count }) {
    if (VIEWS.includes(id)) throw new Error(`Vue déjà existante : ${id}`);
    VIEWS.push(id);
    VIEW_RENDERERS[id] = render;
    EXTRA_VIEWS.push({ id, count });
    const link = document.createElement("a");
    link.href = `#/${id}`;
    link.dataset.view = id;
    link.innerHTML = `${esc(label)}${count ? ' <span class="count">0</span>' : ""}`;
    const nav = document.querySelector("nav.tabs");
    nav.insertBefore(link, nav.querySelector('a[data-view="recherche"]'));
    const section = document.createElement("section");
    section.className = "view";
    section.id = `view-${id}`;
    section.hidden = true;
    document.querySelector("main").appendChild(section);
    if (bind) bind(section);
    if (parseRoute().view === id && S.offers.length) onRoute();
  },
};
```

8. Dans `init`, remplacer `await loadData();` par :

```javascript
    await loadData();
    for (const fn of HOOKS.afterLoad) await fn();
```

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS (le test navigateur passe, ou est sauté si Playwright/Chromium est absent).

- [ ] **Step 5: Vérification manuelle (obligatoire si le test navigateur est sauté)**

Run: `python3 -m jobbot serve --no-browser` puis ouvrir `http://127.0.0.1:8765/` : les 7 onglets existants s'affichent sans erreur dans la console du navigateur, la recherche se lance, le détail d'une offre s'ouvre.

- [ ] **Step 6: Commit et fusion**

```bash
ruff check . && git add jobbot/web/app.js tests/test_web_extensions.py
git commit -m "feat(web): API window.JobBot (registerView, crochets detailExtras/overviewCards/searchExtras/afterLoad)"
git checkout main && git merge --no-ff feat/points-extension -m "Merge feat/points-extension"
git push origin main
```
