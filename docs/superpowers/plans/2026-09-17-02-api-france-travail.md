# API officielle France Travail — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quand l'utilisateur a renseigné ses identifiants francetravail.io, interroger l'API officielle « Offres d'emploi v2 » (données structurées : expérience exigée, jusqu'à 150 offres par requête) à la place de la page web ; sinon, garder automatiquement le scraping actuel.

**Architecture:** `jobbot/features/francetravail_api.py` contient un client OAuth2 (client credentials) avec cache du jeton, la conversion des offres au format colonnes JobSpy déjà consommé par `scraper.row_to_listing`, et un *dispatcher* qui remplace `SOURCES["France Travail"]` : API si identifiants valides, sinon (ou en cas d'erreur) fonction HTML d'origine. Les identifiants viennent des variables d'environnement ou de `config["integrations"]["francetravail"]`. Une carte « Intégrations » est ajoutée dans l'onglet Recherche via `JobBot.hooks.searchExtras`.

**Tech Stack:** Python 3.10+, curl_cffi, pandas ; JavaScript vanilla ; pytest.

## Global Constraints

- **Prérequis :** plan `2026-09-17-00-points-extension.md` fusionné dans `main` (fournit `integrations` dans la config, `public_config` qui masque les secrets, `save_config` qui préserve un secret masqué).
- Branche : `feat/api-france-travail`, créée depuis `main` à jour.
- Ne modifier aucun fichier existant hors `README.md`, `progress/A_FAIRE.md`, `progress/JOURNAL.md`.
- Jeton : `POST https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire`, formulaire `grant_type=client_credentials`, `client_id`, `client_secret`, `scope=api_offresdemploiv2 o2dsoffre`. Réponse : `access_token`, `expires_in` (secondes).
- Recherche : `GET https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search`, en-tête `Authorization: Bearer <jeton>`, paramètres `commune` (INSEE), `distance` (km), `range` (`début-fin`, 150 au plus par requête, début ≤ 3000), et soit `codeROME=J1501`, soit `motsCles`. Statuts 200 (tout) / 206 (partiel, en-tête `Content-Range: offres 0-149/412`) / 204 (aucun résultat).
- Identifiants : variables `FT_CLIENT_ID` / `FT_CLIENT_SECRET` prioritaires, sinon `data/config.json` → `integrations.francetravail.client_id` / `client_secret`.
- Le secret n'est **jamais** renvoyé au navigateur ni écrit dans les journaux.
- Aucun appel réseau dans les tests. `ruff check .` et `pytest -q` passent à chaque tâche.

## File Structure

| Fichier | Rôle |
|---|---|
| `jobbot/features/francetravail_api.py` (créé) | Client OAuth2, conversion, dispatcher de source, routes d'intégration |
| `jobbot/web/features/francetravail_api.js` (créé) | Carte « Intégrations → France Travail » dans Recherche |
| `tests/fixtures/ft_search.json` (créé) | Réponse de recherche (2 offres) |
| `tests/test_francetravail_api.py` (créé) | Tests |

---

### Task 1: Client OAuth2 et conversion des offres

**Files:**
- Create: `jobbot/features/francetravail_api.py`
- Create: `tests/fixtures/ft_search.json`
- Create: `tests/test_francetravail_api.py`

**Interfaces:**
- Consumes: `jobbot.sources._row` (construit une ligne au format colonnes JobSpy), `jobbot.sources.SourceError`
- Produces:
  - `class FTApiError(SourceError)`
  - `class FranceTravailClient(client_id: str, client_secret: str, http=curl_cffi.requests)` avec `token() -> str` (cache jusqu'à `expires_in - 60` s) et `search(params: dict) -> tuple[list[dict], int]` (offres, total)
  - `parse_total(content_range: str | None, count: int) -> int`
  - `offer_to_row(item: dict) -> dict` (clés : `title, company, location, job_type, description, date_posted, job_url, site, salary_text` + colonnes de `_row`)

- [ ] **Step 1: Fixture**

`tests/fixtures/ft_search.json` :

```json
{"resultats": [
  {"id": "213VYRJ", "intitule": "Aide-soignant / Aide-soignante de nuit (H/F)",
   "description": "L'ADAPEI de l'Ariège recrute un aide-soignant de nuit pour son foyer.",
   "dateCreation": "2026-09-14T09:12:00.000Z", "dateActualisation": "2026-09-15T10:00:00.000Z",
   "lieuTravail": {"libelle": "09 - BENAGUES", "latitude": 43.0779, "longitude": 1.6089, "commune": "09050"},
   "romeCode": "J1501", "entreprise": {"nom": "FOYER A DOUBLE TARIFICATION"},
   "typeContrat": "CDI", "typeContratLibelle": "Contrat à durée indéterminée",
   "experienceExige": "D", "experienceLibelle": "Débutant accepté",
   "salaire": {"libelle": "Mensuel de 1900.00 Euros à 2100.00 Euros sur 12 mois"},
   "dureeTravailLibelle": "35H Travail de nuit", "origineOffre": {"urlOrigine": "https://candidat.francetravail.fr/offres/recherche/detail/213VYRJ"}},
  {"id": "212YDGR", "intitule": "Aide-soignant en Espace de Vie Protégé H/F",
   "description": "En tant qu'aide-soignant(e) chez Korian, vous accompagnez les résidents.",
   "dateCreation": "2026-08-26T08:00:00.000Z",
   "lieuTravail": {"libelle": "31 - Rouffiac-Tolosan"},
   "entreprise": {}, "typeContrat": "CDD", "typeContratLibelle": "CDD - 6 Mois",
   "experienceExige": "E", "experienceLibelle": "2 ans - aide-soignant"}
]}
```

- [ ] **Step 2: Tests qui échouent**

`tests/test_francetravail_api.py` :

```python
import json
from pathlib import Path

import pytest

from jobbot.features import francetravail_api as ft

FIX = Path(__file__).parent / "fixtures"


class Resp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, pages):
        self.pages = list(pages)  # réponses successives de GET
        self.posts, self.gets = [], []

    def post(self, url, data=None, headers=None, timeout=None, impersonate=None):
        self.posts.append(data)
        return Resp(200, {"access_token": f"tok{len(self.posts)}", "expires_in": 1499})

    def get(self, url, params=None, headers=None, timeout=None, impersonate=None):
        self.gets.append((dict(params), dict(headers)))
        return self.pages.pop(0)


def fixture():
    return json.loads((FIX / "ft_search.json").read_text(encoding="utf-8"))


def test_token_is_cached_until_expiry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ft.time, "monotonic", lambda: now[0])
    http = FakeHttp([])
    client = ft.FranceTravailClient("id", "secret", http=http)
    assert client.token() == "tok1"
    assert client.token() == "tok1"
    now[0] += 1500  # au-delà de expires_in - 60
    assert client.token() == "tok2"
    assert http.posts[0] == {"grant_type": "client_credentials", "client_id": "id", "client_secret": "secret",
                             "scope": "api_offresdemploiv2 o2dsoffre"}


def test_search_returns_items_and_total():
    http = FakeHttp([Resp(206, fixture(), {"Content-Range": "offres 0-1/412"})])
    items, total = ft.FranceTravailClient("id", "s", http=http).search({"codeROME": "J1501", "range": "0-149"})
    assert len(items) == 2 and total == 412
    params, headers = http.gets[0]
    assert headers["Authorization"] == "Bearer tok1" and params["range"] == "0-149"


def test_search_no_content_and_errors():
    assert ft.FranceTravailClient("id", "s", http=FakeHttp([Resp(204, None)])).search({}) == ([], 0)
    with pytest.raises(ft.FTApiError, match="401"):
        ft.FranceTravailClient("id", "s", http=FakeHttp([Resp(401, {"message": "invalid"})])).search({})


@pytest.mark.parametrize("header,count,expected", [
    ("offres 0-149/412", 150, 412), (None, 37, 37), ("garbage", 5, 5),
])
def test_parse_total(header, count, expected):
    assert ft.parse_total(header, count) == expected


def test_offer_to_row_maps_fields_and_experience():
    first, second = (ft.offer_to_row(i) for i in fixture()["resultats"])
    assert first["title"] == "Aide-soignant / Aide-soignante de nuit (H/F)"
    assert first["company"] == "FOYER A DOUBLE TARIFICATION"
    assert first["location"] == "09 - BENAGUES"
    assert first["job_type"] == "Contrat à durée indéterminée"
    assert first["date_posted"] == "2026-09-14"
    assert first["job_url"] == "https://candidat.francetravail.fr/offres/recherche/detail/213VYRJ"
    assert first["site"] == "France Travail"
    assert first["salary_text"].startswith("Mensuel de 1900")
    assert first["description"].endswith("Expérience : Débutant accepté")
    assert second["company"] == "" and "Expérience : 2 ans - aide-soignant" in second["description"]
```

- [ ] **Step 3: Vérifier l'échec**

Run: `python3 -m pytest tests/test_francetravail_api.py -q`
Expected: FAIL — `ImportError: cannot import name 'francetravail_api'`

- [ ] **Step 4: Implémenter**

`jobbot/features/francetravail_api.py` :

```python
"""
API officielle France Travail « Offres d'emploi v2 » (francetravail.io, gratuite).

Active uniquement si des identifiants sont configurés (variables FT_CLIENT_ID/FT_CLIENT_SECRET,
ou onglet Recherche → Intégrations). Sinon JobBot garde le scraping de la page web.
"""

from __future__ import annotations

import re
import threading
import time

from curl_cffi import requests as cr

from jobbot.sources import SourceError, _row

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
SCOPE = "api_offresdemploiv2 o2dsoffre"
DETAIL_URL = "https://candidat.francetravail.fr/offres/recherche/detail/{id}"
PAGE_SIZE = 150
MAX_START = 3000
TIMEOUT = 30


class FTApiError(SourceError):
    """Erreur de l'API France Travail (identifiants, quota, indisponibilité)."""


def parse_total(content_range: str | None, count: int) -> int:
    m = re.search(r"/(\d+)\s*$", content_range or "")
    return int(m.group(1)) if m else count


class FranceTravailClient:
    def __init__(self, client_id: str, client_secret: str, http=cr):
        self.client_id, self.client_secret, self.http = client_id, client_secret, http
        self._token, self._expires = "", 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
            r = self.http.post(TOKEN_URL, data={
                "grant_type": "client_credentials", "client_id": self.client_id,
                "client_secret": self.client_secret, "scope": SCOPE,
            }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=TIMEOUT)
            if r.status_code != 200:
                raise FTApiError(f"France Travail API : authentification refusée (HTTP {r.status_code})")
            data = r.json()
            self._token = data["access_token"]
            self._expires = time.monotonic() + max(60, int(data.get("expires_in", 1500)) - 60)
            return self._token

    def search(self, params: dict) -> tuple[list[dict], int]:
        r = self.http.get(SEARCH_URL, params=params, headers={"Authorization": f"Bearer {self.token()}",
                                                              "Accept": "application/json"}, timeout=TIMEOUT)
        if r.status_code == 204:
            return [], 0
        if r.status_code not in (200, 206):
            raise FTApiError(f"France Travail API : HTTP {r.status_code}")
        items = (r.json() or {}).get("resultats") or []
        return items, parse_total(r.headers.get("Content-Range"), len(items))


def offer_to_row(item: dict) -> dict:
    description = (item.get("description") or "").strip()
    experience = item.get("experienceLibelle") or ""
    if experience:
        description = f"{description}\n\nExpérience : {experience}".strip()
    return _row(
        title=item.get("intitule") or "",
        company=(item.get("entreprise") or {}).get("nom") or "",
        location=(item.get("lieuTravail") or {}).get("libelle") or "",
        job_type=item.get("typeContratLibelle") or item.get("typeContrat") or "",
        description=description,
        date_posted=(item.get("dateCreation") or "")[:10],
        job_url=DETAIL_URL.format(id=item.get("id", "")),
        site="France Travail",
        salary_text=(item.get("salaire") or {}).get("libelle") or "",
    )
```

- [ ] **Step 5: Lancer les tests**

Run: `python3 -m pytest tests/test_francetravail_api.py -q`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit**

```bash
ruff check . && git add jobbot/features/francetravail_api.py tests/fixtures/ft_search.json tests/test_francetravail_api.py
git commit -m "feat(ft-api): client OAuth2 France Travail et conversion des offres"
```

---

### Task 2: Source « France Travail » : API si identifiants, sinon page web

**Files:**
- Modify: `jobbot/features/francetravail_api.py`
- Modify: `tests/test_francetravail_api.py`

**Interfaces:**
- Consumes: `jobbot.sources.SOURCES`, `jobbot.sources.resolve_insee`, `jobbot.sources._city`, `jobbot.intel.fold`, `jobbot.paths.CONFIG_PATH`
- Produces:
  - `credentials() -> tuple[str, str] | None`
  - `get_client() -> FranceTravailClient | None` (client mis en cache par couple d'identifiants)
  - `scrape_api(client, term: str, city: str, log=None, radius_km: int = 10, max_pages: int = 3) -> pandas.DataFrame`
  - `dispatch(term, city, log=None, radius_km=10, max_pages=3) -> pandas.DataFrame` installé dans `SOURCES["France Travail"]`
  - `HTML_SOURCE` : fonction de scraping d'origine conservée

- [ ] **Step 1: Tests qui échouent** (ajouter)

```python
from jobbot.sources import SOURCES


def test_credentials_env_first_then_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"integrations": {"francetravail": {"client_id": "cid", "client_secret": "csec"}}}), encoding="utf-8")
    monkeypatch.setattr(ft, "CONFIG_PATH", cfg)
    monkeypatch.delenv("FT_CLIENT_ID", raising=False)
    monkeypatch.delenv("FT_CLIENT_SECRET", raising=False)
    assert ft.credentials() == ("cid", "csec")
    monkeypatch.setenv("FT_CLIENT_ID", "eid")
    monkeypatch.setenv("FT_CLIENT_SECRET", "esec")
    assert ft.credentials() == ("eid", "esec")
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("FT_CLIENT_ID")
    assert ft.credentials() is None


def test_scrape_api_paginates_with_rome_or_keywords(monkeypatch):
    monkeypatch.setattr(ft, "resolve_insee", lambda city: "09225")
    page = fixture()
    http = FakeHttp([Resp(206, page, {"Content-Range": "offres 0-149/160"}), Resp(206, page, {"Content-Range": "offres 150-159/160"})])
    client = ft.FranceTravailClient("id", "s", http=http)
    df = ft.scrape_api(client, "aide soignant", "Pamiers, Occitanie", radius_km=12, max_pages=3)
    assert len(df) == 4
    (p1, _), (p2, _) = http.gets
    assert p1 == {"commune": "09225", "distance": 12, "range": "0-149", "codeROME": "J1501"}
    assert p2["range"] == "150-299"
    http2 = FakeHttp([Resp(200, page)])
    ft.scrape_api(ft.FranceTravailClient("id", "s", http=http2), "aide soignant faisant fonction", "Pamiers", max_pages=3)
    assert http2.gets[0][0]["motsCles"] == "aide soignant faisant fonction" and "codeROME" not in http2.gets[0][0]


def test_dispatch_uses_html_without_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr(ft, "get_client", lambda: None)
    monkeypatch.setattr(ft, "HTML_SOURCE", lambda *a, **k: calls.append("html") or "HTML")
    assert ft.dispatch("aide soignant", "Pamiers") == "HTML" and calls == ["html"]


def test_dispatch_falls_back_to_html_on_api_error(monkeypatch):
    class Broken:
        pass

    logs = []
    monkeypatch.setattr(ft, "get_client", lambda: Broken())
    monkeypatch.setattr(ft, "scrape_api", lambda *a, **k: (_ for _ in ()).throw(ft.FTApiError("HTTP 401")))
    monkeypatch.setattr(ft, "HTML_SOURCE", lambda *a, **k: "HTML")
    assert ft.dispatch("aide soignant", "Pamiers", log=logs.append) == "HTML"
    assert any("HTTP 401" in line and "page web" in line for line in logs)


def test_dispatch_installed_as_source():
    assert SOURCES["France Travail"] is ft.dispatch
    assert ft.HTML_SOURCE.__name__ == "scrape_francetravail"
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_francetravail_api.py -q`
Expected: FAIL — `AttributeError: … has no attribute 'credentials'`

- [ ] **Step 3: Implémenter** (ajouter à `jobbot/features/francetravail_api.py`)

Imports supplémentaires :

```python
import json
import os

import pandas as pd

from jobbot.intel import fold
from jobbot.paths import CONFIG_PATH
from jobbot.sources import SOURCES, _city, resolve_insee
```

En fin de fichier :

```python
ROME_AIDE_SOIGNANT = "J1501"
_clients: dict[tuple[str, str], FranceTravailClient] = {}


def credentials() -> tuple[str, str] | None:
    env_id, env_secret = os.environ.get("FT_CLIENT_ID", ""), os.environ.get("FT_CLIENT_SECRET", "")
    if env_id and env_secret:
        return env_id, env_secret
    try:
        conf = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ft_conf = (conf.get("integrations") or {}).get("francetravail") or {}
    cid, secret = ft_conf.get("client_id", ""), ft_conf.get("client_secret", "")
    return (cid, secret) if cid and secret else None


def get_client() -> FranceTravailClient | None:
    creds = credentials()
    if not creds:
        return None
    if creds not in _clients:
        _clients.clear()
        _clients[creds] = FranceTravailClient(*creds)
    return _clients[creds]


def scrape_api(client: FranceTravailClient, term: str, city: str, log=None, radius_km: int = 10, max_pages: int = 3) -> pd.DataFrame:
    code = resolve_insee(_city(city))
    if not code:
        raise FTApiError(f"France Travail API : commune inconnue '{_city(city)}'")
    base = {"commune": code, "distance": radius_km}
    base.update({"motsCles": term} if "faisant" in fold(term) else {"codeROME": ROME_AIDE_SOIGNANT})
    rows: list[dict] = []
    for page in range(max_pages):
        start = page * PAGE_SIZE
        if start > MAX_START:
            break
        items, total = client.search({**base, "range": f"{start}-{start + PAGE_SIZE - 1}"})
        rows.extend(offer_to_row(i) for i in items)
        if start + PAGE_SIZE >= total or not items:
            break
    return pd.DataFrame(rows)


HTML_SOURCE = SOURCES["France Travail"]


def dispatch(term: str, city: str, log=None, radius_km: int = 10, max_pages: int = 3) -> pd.DataFrame:
    client = get_client()
    if client is None:
        return HTML_SOURCE(term, city, log=log, radius_km=radius_km, max_pages=max_pages)
    try:
        return scrape_api(client, term, city, log=log, radius_km=radius_km, max_pages=max_pages)
    except FTApiError as e:
        if log:
            log(f"  {e} → repli sur la page web France Travail")
        return HTML_SOURCE(term, city, log=log, radius_km=radius_km, max_pages=max_pages)


SOURCES["France Travail"] = dispatch
```

Remarque : `HTML_SOURCE = SOURCES["France Travail"]` est évalué à l'import ; le module n'est importé qu'une fois (chargement des fonctionnalités), donc `HTML_SOURCE` est bien `scrape_francetravail`.

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff check . && git add jobbot/features/francetravail_api.py tests/test_francetravail_api.py
git commit -m "feat(ft-api): source France Travail via l'API officielle avec repli sur la page web"
```

---

### Task 3: Routes d'intégration et carte « Intégrations » dans Recherche

**Files:**
- Modify: `jobbot/features/francetravail_api.py`
- Modify: `tests/test_francetravail_api.py`
- Create: `jobbot/web/features/francetravail_api.js`
- Modify: `README.md`, `progress/A_FAIRE.md`, `progress/JOURNAL.md`

**Interfaces:**
- Consumes: `extensions.route`, `server.load_config`, `server.save_config` (import local), `window.JobBot.hooks.searchExtras`
- Produces:
  - `GET /api/integrations/francetravail` → `200 {"configured": bool, "origin": "env" | "config" | null, "client_id": str (vide si env), "secret_hint": "••••1234" | ""}`
  - `POST /api/integrations/francetravail` corps `{client_id, client_secret}` → `200` même format ; `400` si `client_id` vide
  - `POST /api/integrations/francetravail/test` → `200 {"ok": bool, "message": str}` (jeton + recherche `range=0-0` à Pamiers)

- [ ] **Step 1: Tests qui échouent** (ajouter)

```python
from jobbot import extensions, server


def call(method, path, body=None):
    route, match = extensions.find_route(method, path)
    return route.handler(None, match, {}, body or {})


def test_integration_routes_save_and_mask(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(ft, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.delenv("FT_CLIENT_ID", raising=False)
    monkeypatch.delenv("FT_CLIENT_SECRET", raising=False)
    assert call("GET", "/api/integrations/francetravail") == (200, {"configured": False, "origin": None, "client_id": "", "secret_hint": ""})
    assert call("POST", "/api/integrations/francetravail", {"client_id": ""})[0] == 400
    status, payload = call("POST", "/api/integrations/francetravail", {"client_id": "PAR_x", "client_secret": "abcd-5678"})
    assert status == 200 and payload == {"configured": True, "origin": "config", "client_id": "PAR_x", "secret_hint": "••••5678"}
    assert "abcd-5678" not in json.dumps(payload)
    # renvoyer le secret masqué ne l'écrase pas
    call("POST", "/api/integrations/francetravail", {"client_id": "PAR_x", "client_secret": "••••5678"})
    assert ft.credentials() == ("PAR_x", "abcd-5678")


def test_integration_test_route(monkeypatch):
    class OkClient:
        def search(self, params):
            assert params["range"] == "0-0"
            return [], 0

    monkeypatch.setattr(ft, "get_client", lambda: OkClient())
    monkeypatch.setattr(ft, "resolve_insee", lambda city: "09225")
    assert call("POST", "/api/integrations/francetravail/test") == (200, {"ok": True, "message": "Connexion réussie à l'API France Travail."})
    monkeypatch.setattr(ft, "get_client", lambda: None)
    assert call("POST", "/api/integrations/francetravail/test")[1]["ok"] is False
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_francetravail_api.py -q`
Expected: FAIL — `TypeError: cannot unpack non-iterable NoneType object` (route introuvable)

- [ ] **Step 3: Implémenter les routes** (ajouter à `jobbot/features/francetravail_api.py`)

Import supplémentaire : `from jobbot import extensions`.

```python
def _status() -> dict:
    env = bool(os.environ.get("FT_CLIENT_ID") and os.environ.get("FT_CLIENT_SECRET"))
    creds = credentials()
    if not creds:
        return {"configured": False, "origin": None, "client_id": "", "secret_hint": ""}
    return {"configured": True, "origin": "env" if env else "config",
            "client_id": "" if env else creds[0], "secret_hint": "••••" + creds[1][-4:]}


@extensions.route("GET", r"/api/integrations/francetravail")
def _route_get(req, match, query, body):
    return 200, _status()


@extensions.route("POST", r"/api/integrations/francetravail")
def _route_save(req, match, query, body):
    from jobbot.server import load_config, save_config

    client_id = str(body.get("client_id") or "").strip()
    if not client_id:
        return 400, {"error": "Identifiant client manquant."}
    # Repartir de la config enregistrée (non masquée) pour ne pas remettre les autres réglages à zéro ;
    # save_config conserve l'ancien secret si la valeur reçue est le masque « ••••1234 ».
    current = load_config()
    current["integrations"] = {**current.get("integrations", {}), "francetravail": {
        "client_id": client_id, "client_secret": str(body.get("client_secret") or "").strip()}}
    save_config(current)
    _clients.clear()
    return 200, _status()


@extensions.route("POST", r"/api/integrations/francetravail/test")
def _route_test(req, match, query, body):
    client = get_client()
    if client is None:
        return 200, {"ok": False, "message": "Aucun identifiant configuré."}
    try:
        client.search({"commune": resolve_insee("Pamiers") or "09225", "distance": 10,
                       "codeROME": ROME_AIDE_SOIGNANT, "range": "0-0"})
    except FTApiError as e:
        return 200, {"ok": False, "message": str(e)}
    return 200, {"ok": True, "message": "Connexion réussie à l'API France Travail."}
```

- [ ] **Step 4: Carte front**

`jobbot/web/features/francetravail_api.js` :

```javascript
/* Intégration : API officielle France Travail (onglet Recherche) */
(() => {
  const J = window.JobBot;
  J.hooks.searchExtras.push(async (container) => {
    const card = document.createElement("div");
    card.className = "card";
    container.appendChild(card);

    async function render(message) {
      let st;
      try { st = await J.api("/api/integrations/francetravail"); } catch { st = { configured: false }; }
      card.innerHTML = `
        <div class="card-head"><div><h3>Intégrations · API France Travail</h3>
          <p class="card-sub">${st.configured
            ? `<span class="st good"><span class="sd" aria-hidden="true"></span>${J.icon("check")}Activée${st.origin === "env" ? " (variables d'environnement)" : ""}</span>`
            : `<span class="st idle"><span class="sd" aria-hidden="true"></span>Non configurée : JobBot lit la page web de France Travail</span>`}</p></div></div>
        <p class="help" style="margin:0 0 10px">Compte gratuit sur <a href="https://francetravail.io" target="_blank" rel="noopener">francetravail.io</a>
          → créer une application → ajouter « Offres d'emploi v2 ». Données plus fiables (expérience exigée, 150 offres par requête).</p>
        ${st.origin === "env" ? "" : `
        <div class="form-grid" style="gap:10px">
          <div class="form-row"><label for="ft-id">Identifiant client</label><input type="text" id="ft-id" autocomplete="off" value="${J.esc(st.client_id || "")}"/></div>
          <div class="form-row"><label for="ft-secret">Clé secrète</label><input type="password" id="ft-secret" autocomplete="off" placeholder="${J.esc(st.secret_hint || "")}"/></div>
          <div class="inline">
            <button type="button" class="btn-primary btn-sm" id="ft-save">Enregistrer</button>
            <button type="button" class="btn-ghost btn-sm" id="ft-test" ${st.configured ? "" : "disabled"}>Tester la connexion</button>
            <span class="save-state" id="ft-msg" aria-live="polite">${J.esc(message || "")}</span>
          </div>
        </div>`}`;
    }

    card.addEventListener("click", async (e) => {
      if (e.target.id === "ft-save") {
        const secretInput = card.querySelector("#ft-secret");
        try {
          await J.api("/api/integrations/francetravail", {
            client_id: card.querySelector("#ft-id").value,
            client_secret: secretInput.value || secretInput.placeholder,
          });
          await render("Enregistré.");
        } catch (err) { card.querySelector("#ft-msg").textContent = err.message; }
      }
      if (e.target.id === "ft-test") {
        card.querySelector("#ft-msg").textContent = "Test en cours…";
        const r = await J.api("/api/integrations/francetravail/test", {});
        card.querySelector("#ft-msg").textContent = r.message;
      }
    });
    await render();
  });
})();
```

- [ ] **Step 5: Vérifier dans le navigateur**

Run: `python3 -m jobbot serve --no-browser`, ouvrir `#/recherche`.
Expected: carte « Intégrations · API France Travail » en haut de la colonne de droite, statut « Non configurée ». Saisir des valeurs factices → « Enregistré », le champ secret affiche `••••xxxx` en indication ; « Tester la connexion » affiche « France Travail API : authentification refusée (HTTP 401) » (ou 400). `curl -s http://127.0.0.1:8765/api/config | grep -c client_secret` ne montre que la valeur masquée. Supprimer ensuite les valeurs factices de `data/config.json`.

- [ ] **Step 6: Documentation**

`README.md`, section Configuration, ajouter la ligne au tableau :

```markdown
| `integrations.francetravail` | vide | Identifiants francetravail.io (API officielle, optionnelle) ; ou variables `FT_CLIENT_ID` / `FT_CLIENT_SECRET` |
```

`progress/A_FAIRE.md` : à l'étape 5, confirmer le chemin exact « Recherche → Intégrations · API France Travail ».
`progress/JOURNAL.md` : ajouter `| 2026-09-17 | Plan 02 — API officielle France Travail (optionnelle) | ✅ |`.

- [ ] **Step 7: Tests, lint, commit**

```bash
python3 -m pytest -q && ruff check .
git add jobbot/features/francetravail_api.py jobbot/web/features/francetravail_api.js tests/test_francetravail_api.py README.md progress/
git commit -m "feat(ft-api): réglage des identifiants et test de connexion dans Recherche"
```
