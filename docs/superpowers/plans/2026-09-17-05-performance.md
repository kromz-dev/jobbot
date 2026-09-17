# Performance du tableau de bord — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Diviser par plus de 5 le poids des échanges du tableau de bord (aujourd'hui ~1,4 Mo par rafraîchissement, liste d'offres reconstruite deux fois, journal complet renvoyé chaque seconde) sans changer ce que voit l'utilisateur.

**Architecture:** Quatre optimisations indépendantes et mesurées : (1) compression gzip des réponses du serveur, (2) liste d'offres « légère » (descriptions tronquées, texte complet chargé à l'ouverture du détail), (3) cache des offres et employeurs par version des données, (4) journal envoyé par incréments.

**Tech Stack:** Python 3.10+ stdlib (gzip, http.server), JavaScript vanilla, pytest.

## Global Constraints

- **Prérequis :** plan `2026-09-17-00-points-extension.md` fusionné dans `main`.
- Branche : `perf/tableau-de-bord`, créée depuis `main` à jour.
- Fichiers du cœur modifiés : `jobbot/server.py`, `jobbot/db.py`, `jobbot/web/app.js` uniquement (+ `progress/JOURNAL.md`).
- Aucune modification du format JSON existant sauf ajouts : champ `description_truncated` sur les offres de `/api/offers`, champs `logs_offset` et `logs_total` dans `/api/state`.
- Seuil de compression : corps > 1 024 octets, types `application/json`, `text/html`, `text/css`, `text/javascript`, `text/csv`, seulement si le client envoie `Accept-Encoding: gzip`.
- Description tronquée à **600 caractères** dans la liste légère.
- Les décorateurs d'offres (`extensions.OFFER_DECORATORS`, ex. plan 03) doivent toujours s'appliquer : le cache se situe **au-dessus** de `db.list_offers`.
- `ruff check .` et `pytest -q` passent à chaque tâche.

## File Structure

| Fichier | Rôle |
|---|---|
| `jobbot/server.py` (modifié) | gzip, cache par `data_version`, journal incrémental |
| `jobbot/db.py` (modifié) | `list_offers(light=...)`, `companies(offers=...)` |
| `jobbot/web/app.js` (modifié) | description complète à la demande, fusion du journal incrémental |
| `tests/test_performance.py` (créé) | Tests |

---

### Task 1: Compression gzip des réponses

**Files:**
- Modify: `jobbot/server.py` (méthode `Handler._send`)
- Create: `tests/test_performance.py`

**Interfaces:**
- Consumes: `server.Handler`
- Produces: `server.COMPRESSIBLE: tuple[str, ...]`, `server.MIN_GZIP_BYTES = 1024` ; en-têtes `Content-Encoding: gzip` et `Vary: Accept-Encoding` quand compressé

- [ ] **Step 1: Tests qui échouent**

`tests/test_performance.py` :

```python
import gzip
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jobbot import db, server


@pytest.fixture
def http_server(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def get(url, gzip_ok=True):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"} if gzip_ok else {})
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return r.status, dict(r.headers), (gzip.decompress(raw) if r.headers.get("Content-Encoding") == "gzip" else raw)


def seed_offers(n=40, desc_len=3000):
    run = db.start_run({})
    db.ingest([{"apply_url": f"https://x/{i}", "source": "Hellowork", "title": f"Aide-soignant poste {i} service {i}",
                "company": f"Ehpad {i}", "location": "Pamiers - 09", "search_hub": "Pamiers",
                "description": ("Description détaillée du poste. " * 200)[:desc_len]} for i in range(n)], run)


def test_large_json_is_gzipped(http_server):
    seed_offers()
    status, headers, body = get(f"{http_server}/api/offers")
    assert status == 200 and headers["Content-Encoding"] == "gzip" and headers["Vary"] == "Accept-Encoding"
    assert len(json.loads(body)) == 40


def test_no_gzip_without_accept_encoding_or_small_body(http_server):
    seed_offers()
    _, headers, body = get(f"{http_server}/api/offers", gzip_ok=False)
    assert "Content-Encoding" not in headers and json.loads(body)
    _, headers, _ = get(f"{http_server}/api/runs")  # petite réponse
    assert "Content-Encoding" not in headers
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_performance.py -q`
Expected: FAIL — `KeyError: 'Content-Encoding'`

- [ ] **Step 3: Implémenter** (dans `jobbot/server.py`)

Ajouter `import gzip` aux imports, puis après `STATIC_TYPES = …` :

```python
COMPRESSIBLE = ("application/json", "text/html", "text/css", "text/javascript", "text/csv")
MIN_GZIP_BYTES = 1024
```

Remplacer la méthode `_send` :

```python
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        headers = dict(extra or {})
        if (len(body) > MIN_GZIP_BYTES and ctype.startswith(COMPRESSIBLE)
                and "gzip" in self.headers.get("Accept-Encoding", "")):
            body = gzip.compress(body, compresslevel=5)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
```

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff check . && git add jobbot/server.py tests/test_performance.py
git commit -m "perf(server): compression gzip des réponses volumineuses"
```

---

### Task 2: Liste d'offres légère et description complète à la demande

**Files:**
- Modify: `jobbot/db.py` (`list_offers`)
- Modify: `jobbot/server.py` (route `/api/offers`)
- Modify: `jobbot/web/app.js` (clic d'expansion dans `bindOffers`)
- Modify: `tests/test_performance.py`

**Interfaces:**
- Consumes: `db.get_offer(offer_id)` (description complète, inchangée)
- Produces:
  - `db.list_offers(include_inactive: bool = True, path=None, light: bool = False) -> list[dict]` ; avec `light=True`, `description` ≤ 600 caractères et `description_truncated: bool` sur chaque offre ; avec `light=False`, `description_truncated` vaut `False`
  - `GET /api/offers` renvoie la liste légère

- [ ] **Step 1: Tests qui échouent** (ajouter)

```python
def test_light_list_truncates_descriptions(tmp_db):
    seed_offers(n=3, desc_len=3000)
    full = db.list_offers()
    light = db.list_offers(light=True)
    assert all(not o["description_truncated"] and len(o["description"]) > 600 for o in full)
    assert all(o["description_truncated"] and len(o["description"]) == 600 for o in light)
    assert db.get_offer(light[0]["id"])["description"] == full[0]["description"]


def test_api_offers_payload_is_much_smaller(http_server):
    seed_offers(n=40, desc_len=3000)
    _, _, light_body = get(f"{http_server}/api/offers", gzip_ok=False)
    full_size = len(json.dumps(db.list_offers(include_inactive=False), ensure_ascii=False).encode())
    assert len(light_body) < full_size / 2
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_performance.py -q`
Expected: FAIL — `TypeError: list_offers() got an unexpected keyword argument 'light'`

- [ ] **Step 3: Implémenter dans `jobbot/db.py`**

Remplacer la signature et la fin de `list_offers` :

```python
@retry_io
def list_offers(include_inactive: bool = True, path=None, light: bool = False) -> list[dict]:
```

et, juste avant la boucle des décorateurs (`for decorate in extensions.OFFER_DECORATORS:`), ajouter :

```python
    for o in result:
        o["description_truncated"] = bool(light and len(o["description"] or "") > LIGHT_DESCRIPTION_CHARS)
        if o["description_truncated"]:
            o["description"] = o["description"][:LIGHT_DESCRIPTION_CHARS]
```

Ajouter la constante après `MATCH_WINDOW_DAYS = 60` :

```python
LIGHT_DESCRIPTION_CHARS = 600
```

- [ ] **Step 4: Route `/api/offers` dans `jobbot/server.py`**

Remplacer :

```python
            self._json(200, db.list_offers(include_inactive=query.get("active", ["0"])[0] != "1"))
```

par :

```python
            self._json(200, db.list_offers(include_inactive=query.get("active", ["0"])[0] != "1", light=True))
```

- [ ] **Step 5: Front — charger le texte complet à l'ouverture (`jobbot/web/app.js`)**

Dans `bindOffers`, remplacer la ligne :

```javascript
    if (ex) { const id = +ex.dataset.expand; S.expanded.has(id) ? S.expanded.delete(id) : S.expanded.add(id); renderOffers(); return; }
```

par :

```javascript
    if (ex) {
      const id = +ex.dataset.expand;
      if (S.expanded.has(id)) S.expanded.delete(id);
      else {
        S.expanded.add(id);
        const o = S.offers.find(x => x.id === id);
        if (o && o.description_truncated) {
          try { const full = await api(`/api/offers/${id}`); o.description = full.description; o.description_truncated = false; } catch {}
        }
      }
      renderOffers();
      return;
    }
```

Dans `filteredOffers`, la recherche plein texte porte sur la description tronquée : c'est accepté (600 premiers caractères = l'essentiel de l'annonce).

- [ ] **Step 6: Lancer les tests et vérifier dans le navigateur**

Run: `python3 -m pytest -q`
Expected: PASS.
Puis `python3 -m jobbot serve --no-browser` : ouvrir le détail d'une offre longue → la description complète s'affiche ; dans les outils de développement (onglet Réseau), `/api/offers` est compressé (`content-encoding: gzip`) et nettement plus léger qu'avant.

- [ ] **Step 7: Commit**

```bash
ruff check . && git add jobbot/db.py jobbot/server.py jobbot/web/app.js tests/test_performance.py
git commit -m "perf: liste d'offres légère, description complète chargée à l'ouverture"
```

---

### Task 3: Cache des offres et des employeurs par version des données

**Files:**
- Modify: `jobbot/db.py` (`companies`)
- Modify: `jobbot/server.py` (routes `/api/offers`, `/api/companies`)
- Modify: `tests/test_performance.py`

**Interfaces:**
- Consumes: `db.data_version() -> str`, `db.list_offers(..., light=True)`
- Produces:
  - `db.companies(path=None, offers: list[dict] | None = None) -> list[dict]` (réutilise `offers` si fourni, sinon appelle `list_offers(True, path)`)
  - `server.cached_offers() -> list[dict]` : liste légère **avec** offres inactives, recalculée seulement si `db.data_version()` change ; `/api/offers` filtre les inactives côté serveur si `?active=1`
  - `server.cached_companies() -> list[dict]`
  - `server.reset_cache() -> None` (utilisé par les tests)

- [ ] **Step 1: Tests qui échouent**

Dans la fixture `http_server` de `tests/test_performance.py`, ajouter `server.reset_cache()` juste avant `httpd = ThreadingHTTPServer(...)` (deux tests peuvent produire la même `data_version` dans la même seconde avec des bases différentes) :

```python
@pytest.fixture
def http_server(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    server.reset_cache()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
```

Puis ajouter :

```python
def test_offers_and_companies_computed_once_per_version(tmp_db, monkeypatch):
    seed_offers(n=5)
    server.reset_cache()
    calls = []
    real = db.list_offers
    monkeypatch.setattr(db, "list_offers", lambda *a, **k: calls.append(k) or real(*a, **k))
    first = server.cached_offers()
    server.cached_companies()
    server.cached_offers()
    assert len(calls) == 1 and len(first) == 5
    db.update_offer(first[0]["id"], status="favori")  # change data_version
    assert server.cached_offers()[0]["id"] and len(calls) == 2


def test_companies_accepts_precomputed_offers(tmp_db, monkeypatch):
    seed_offers(n=3)
    offers = db.list_offers()
    monkeypatch.setattr(db, "list_offers", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ne doit pas être appelé")))
    assert len(db.companies(offers=offers)) == 3
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_performance.py -q`
Expected: FAIL — `AttributeError: module 'jobbot.server' has no attribute 'reset_cache'`

- [ ] **Step 3: Implémenter `db.companies(offers=…)`**

Dans `jobbot/db.py`, remplacer :

```python
def companies(path=None) -> list[dict]:
    """Vue employeurs : volume, récurrence, candidatures déjà envoyées."""
    groups: dict[str, dict] = {}
    for o in list_offers(True, path):
```

par :

```python
def companies(path=None, offers: list[dict] | None = None) -> list[dict]:
    """Vue employeurs : volume, récurrence, candidatures déjà envoyées."""
    groups: dict[str, dict] = {}
    for o in (offers if offers is not None else list_offers(True, path)):
```

- [ ] **Step 4: Cache dans `jobbot/server.py`**

Après `_lock = threading.Lock()` :

```python
_cache_lock = threading.Lock()
_cache: dict = {"version": None, "offers": None, "companies": None}


def reset_cache() -> None:
    with _cache_lock:
        _cache.update(version=None, offers=None, companies=None)


def _refresh_cache() -> None:
    version = db.data_version()
    if _cache["version"] != version:
        offers = db.list_offers(include_inactive=True, light=True)
        _cache.update(version=version, offers=offers, companies=db.companies(offers=offers))


def cached_offers() -> list[dict]:
    with _cache_lock:
        _refresh_cache()
        return _cache["offers"]


def cached_companies() -> list[dict]:
    with _cache_lock:
        _refresh_cache()
        return _cache["companies"]
```

Remplacer les routes :

```python
        elif path == "/api/offers":
            offers = cached_offers()
            if query.get("active", ["0"])[0] == "1":
                offers = [o for o in offers if o["is_active"]]
            self._json(200, offers)
```

et

```python
        elif path == "/api/companies":
            self._json(200, cached_companies())
```

Remarque : `data_version()` inclut le nombre d'offres, la dernière mise à jour, le nombre d'annonces et le dernier identifiant d'historique de statut ; une note modifiée met à jour `updated_at` → le cache est invalidé.

- [ ] **Step 5: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
ruff check . && git add jobbot/db.py jobbot/server.py tests/test_performance.py
git commit -m "perf(server): cache des offres et employeurs par version des données"
```

---

### Task 4: Journal incrémental dans `/api/state`

**Files:**
- Modify: `jobbot/server.py` (route `/api/state`)
- Modify: `jobbot/web/app.js` (`poll`)
- Modify: `tests/test_performance.py`, `progress/JOURNAL.md`

**Interfaces:**
- Consumes: `server.state` (`logs`, `started_at`)
- Produces: `GET /api/state?logs_from=<n>&logs_run=<started_at>` → si `logs_run == state["started_at"]` et `0 ≤ n ≤ len(logs)` : `logs` = `logs[n:]`, `logs_offset` = n ; sinon `logs` complet, `logs_offset` = 0. Toujours `logs_total` = nombre total de lignes.

- [ ] **Step 1: Tests qui échouent** (ajouter)

```python
def test_state_returns_incremental_logs(http_server, monkeypatch):
    with server._lock:
        server.state["started_at"] = "2026-09-17T10:00:00"
        server.state["logs"] = [{"t": "10:00:0%d" % i, "msg": f"ligne {i}"} for i in range(5)]
    _, _, body = get(f"{http_server}/api/state?logs_from=3&logs_run=2026-09-17T10:00:00")
    data = json.loads(body)
    assert [l["msg"] for l in data["logs"]] == ["ligne 3", "ligne 4"]
    assert data["logs_offset"] == 3 and data["logs_total"] == 5
    _, _, body = get(f"{http_server}/api/state?logs_from=3&logs_run=autre-recherche")
    data = json.loads(body)
    assert len(data["logs"]) == 5 and data["logs_offset"] == 0
    _, _, body = get(f"{http_server}/api/state")
    assert json.loads(body)["logs_offset"] == 0
    with server._lock:
        server.state["logs"], server.state["started_at"] = [], None
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_performance.py -q`
Expected: FAIL — `KeyError: 'logs_offset'`

- [ ] **Step 3: Implémenter dans `jobbot/server.py`**

Dans la route `/api/state`, remplacer :

```python
            with _lock:
                snap = {k: (list(v) if k == "logs" else dict(v) if isinstance(v, dict) else v) for k, v in state.items()}
```

par :

```python
            with _lock:
                snap = {k: (dict(v) if isinstance(v, dict) else v) for k, v in state.items() if k != "logs"}
                logs = state["logs"]
                try:
                    start = int(query.get("logs_from", ["0"])[0])
                except ValueError:
                    start = 0
                same_run = query.get("logs_run", [None])[0] == state["started_at"]
                offset = start if same_run and 0 <= start <= len(logs) else 0
                snap["logs"] = logs[offset:]
                snap["logs_offset"] = offset
                snap["logs_total"] = len(logs)
```

- [ ] **Step 4: Front — fusionner le journal (`jobbot/web/app.js`)**

Dans `poll`, remplacer :

```javascript
    S.data = await api("/api/state");
```

par :

```javascript
    const previous = S.data || {};
    const known = previous.logs || [];
    const run = previous.started_at ? `&logs_run=${encodeURIComponent(previous.started_at)}` : "";
    const next = await api(`/api/state?logs_from=${known.length}${run}`);
    next.logs = next.logs_offset > 0 ? known.slice(0, next.logs_offset).concat(next.logs) : next.logs;
    S.data = next;
```

(`renderLog` lit toujours `S.data.logs` et reste inchangée.)

- [ ] **Step 5: Tests et vérification**

Run: `python3 -m pytest -q`
Expected: PASS.
Puis lancer une recherche depuis le tableau de bord : le journal de l'onglet Recherche défile normalement, sans doublons ; dans l'onglet Réseau, chaque `/api/state` ne transporte que les nouvelles lignes. Lancer une seconde recherche : le journal repart de zéro.

- [ ] **Step 6: Mesure finale et commit**

Run :

```bash
python3 - <<'PY'
import json, gzip
from jobbot import db
full = len(json.dumps(db.list_offers(include_inactive=True), ensure_ascii=False).encode())
light = json.dumps(db.list_offers(include_inactive=True, light=True), ensure_ascii=False).encode()
print(f"avant {full/1024:.0f} Ko → après {len(light)/1024:.0f} Ko, gzip {len(gzip.compress(light, 5))/1024:.0f} Ko")
PY
```

Expected: le volume compressé est au moins 5 fois plus petit que « avant ». Reporter la mesure dans `progress/JOURNAL.md` :

```markdown
| 2026-09-17 | Plan 05 — Performance (gzip, liste légère, cache, journal incrémental) : <avant> Ko → <après gzip> Ko | ✅ |
```

```bash
ruff check . && git add jobbot/server.py jobbot/web/app.js tests/test_performance.py progress/JOURNAL.md
git commit -m "perf: journal incrémental dans /api/state"
```
