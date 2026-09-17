# Marché caché (La Bonne Boîte) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lister les établissements autour de chaque ville qui ont une forte probabilité de recruter des aides-soignants (La Bonne Boîte, France Travail), avec coordonnées, distance, offres en ligne liées et suivi des candidatures spontanées.

**Architecture:** Une fonctionnalité autonome `jobbot/features/prospects.py` (client HTTP sans clé vers l'API publique `https://labonneboite.francetravail.fr/api/v2/…`, table SQLite `prospects`, routes, crochet post-recherche) et une vue front `jobbot/web/features/prospects.js` enregistrée via `JobBot.registerView`. Aucun fichier du cœur n'est modifié.

**Tech Stack:** Python 3.10+, curl_cffi, sqlite3, rapidfuzz ; JavaScript vanilla ; pytest.

## Global Constraints

- **Prérequis :** le plan `2026-09-17-00-points-extension.md` est fusionné dans `main`.
- Branche : `feat/marche-cache`, créée depuis `main` à jour.
- Ne modifier **aucun** fichier existant hors `README.md` et `progress/JOURNAL.md` (tout passe par `jobbot/features/` et `jobbot/web/features/`).
- API publique, **sans clé** : `GET https://labonneboite.francetravail.fr/api/v2/search` (paramètres `rome`, `citycode`, `distance`, `page`, `page_size`, `sort_by=romes.hiring_potential`, `sort_direction=desc`) et `GET /api/v2/company/siret/<siret>`.
- Code ROME aide-soignant : `J1501`.
- Politesse réseau : au plus 1 requête à la fois vers La Bonne Boîte, 3 essais avec attente exponentielle (1,5 s, 3 s) sur 429/5xx.
- Aucun appel réseau dans les tests (client injectable / monkeypatch).
- `ruff check .` et `pytest -q` passent à la fin de chaque tâche. Textes en français.

## File Structure

| Fichier | Rôle |
|---|---|
| `jobbot/features/prospects.py` (créé) | Client La Bonne Boîte, stockage, rapprochement avec les offres, routes, crochet |
| `jobbot/web/features/prospects.js` (créé) | Vue « Établissements » + carte dans la vue d'ensemble |
| `tests/fixtures/lbb_search.json` (créé) | Réponse de recherche (extrait réel anonymisable : données publiques d'établissements) |
| `tests/fixtures/lbb_company.json` (créé) | Réponse de détail d'établissement |
| `tests/test_prospects.py` (créé) | Tests |

---

### Task 1: Client La Bonne Boîte (recherche + détail + extraction des contacts)

**Files:**
- Create: `jobbot/features/prospects.py`
- Create: `tests/fixtures/lbb_search.json`
- Create: `tests/fixtures/lbb_company.json`
- Create: `tests/test_prospects.py`

**Interfaces:**
- Consumes: rien du cœur pour cette tâche.
- Produces:
  - `class ProspectError(Exception)`
  - `prospects.get_json(path: str, params: dict | None = None) -> dict` (seul point réseau ; monkeypatché en test)
  - `prospects.search_establishments(citycode: str, distance_km: int, rome: str = "J1501", page_size: int = 50, max_pages: int = 5) -> list[dict]` ; chaque dict : `siret, name, naf, naf_label, city, citycode, postcode, lat, lon, headcount, hiring_potential (float, 1 décimale), high_potential (bool)`
  - `prospects.company_details(siret: str) -> dict` ; clés : `address, email, phone, website`
  - `prospects.extract_contacts(*values: str) -> tuple[str, str]` → `(email, phone)`

- [ ] **Step 1: Créer les fixtures**

`tests/fixtures/lbb_search.json` :

```json
{
  "hits": 3,
  "items": [
    {"rome": "J1501", "id": 3555178, "siret": "26090012100013", "email": "yes", "company_name": "EHPAD DE MIREPOIX",
     "office_name": "", "headcount_min": 50, "headcount_max": 99, "naf": "8710A",
     "naf_label": "Hébergement médicalisé pour personnes âgées", "location": {"lat": 43.0885, "lon": 1.87247},
     "city": "Mirepoix", "citycode": "09194", "postcode": "09500", "department": "Ariège", "region": "Occitanie",
     "department_number": "9", "hiring_potential": 83.59911732797187, "is_high_potential": true},
    {"rome": "J1501", "id": 1, "siret": "77553012300019", "email": "no", "company_name": "ASSOCIATION ST JOSEPH VERNIOLLE",
     "office_name": "", "headcount_min": 20, "headcount_max": 49, "naf": "8710A",
     "naf_label": "Hébergement médicalisé pour personnes âgées", "location": {"lat": 43.0816, "lon": 1.6531},
     "city": "Verniolle", "citycode": "09329", "postcode": "09340", "department": "Ariège", "region": "Occitanie",
     "department_number": "9", "hiring_potential": 87.3751661992451, "is_high_potential": true},
    {"rome": "J1501", "id": 2, "siret": "41234567800012", "email": "no", "company_name": "PROMAID",
     "office_name": "Agence Pamiers", "headcount_min": null, "headcount_max": null, "naf": "8810A",
     "naf_label": "Aide à domicile", "location": {"lat": 43.1301, "lon": 1.6402},
     "city": "La Tour-du-Crieu", "citycode": "09312", "postcode": "09100", "department": "Ariège", "region": "Occitanie",
     "department_number": "9", "hiring_potential": 5.6556197877, "is_high_potential": false}
  ]
}
```

`tests/fixtures/lbb_company.json` :

```json
{"date_created": "2023-12-06T14:37:33", "id": 3555178, "siret": "26090012100013", "naf": "8710A",
 "company_name": "EHPAD DE MIREPOIX", "office_name": "", "streetnumber": "22", "street": "rue Monseigneur de Cambon",
 "postcode": "09500", "citycode": "09194", "email": "yes", "phone": "mr.mirepoix@wanadoo.fr", "website": "",
 "headcount_range": "50-99", "city": "Mirepoix", "latitude": 43.0885, "longitude": 1.87247,
 "naf_label": "Hébergement médicalisé pour personnes âgées", "hide_location": false}
```

- [ ] **Step 2: Écrire les tests qui échouent**

`tests/test_prospects.py` :

```python
import json
from pathlib import Path

import pytest

from jobbot.features import prospects

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture
def fake_lbb(monkeypatch):
    calls = []

    def fake_get_json(path, params=None):
        calls.append((path, dict(params or {})))
        if path == "/api/v2/search":
            data = load("lbb_search.json")
            return data if params["page"] == 1 else {"hits": 3, "items": []}
        if path.startswith("/api/v2/company/siret/"):
            return load("lbb_company.json")
        raise AssertionError(path)

    monkeypatch.setattr(prospects, "get_json", fake_get_json)
    return calls


def test_search_establishments_parses_and_paginates(fake_lbb):
    items = prospects.search_establishments("09225", 10, page_size=3)
    assert [i["siret"] for i in items] == ["26090012100013", "77553012300019", "41234567800012"]
    first = items[0]
    assert first["name"] == "EHPAD DE MIREPOIX"
    assert first["hiring_potential"] == 83.6
    assert first["high_potential"] is True
    assert first["headcount"] == "50-99"
    assert (first["lat"], first["lon"]) == (43.0885, 1.87247)
    assert items[2]["name"] == "PROMAID — Agence Pamiers" and items[2]["headcount"] == ""
    path, params = fake_lbb[0]
    assert params == {"rome": "J1501", "citycode": "09225", "distance": 10, "page": 1, "page_size": 3,
                      "sort_by": "romes.hiring_potential", "sort_direction": "desc"}
    assert len(fake_lbb) == 2  # page 2 vide → arrêt


def test_search_stops_when_page_not_full(fake_lbb):
    prospects.search_establishments("09225", 10, page_size=50)
    assert len(fake_lbb) == 1


def test_company_details_extracts_contacts(fake_lbb):
    d = prospects.company_details("26090012100013")
    assert d == {"address": "22 rue Monseigneur de Cambon, 09500 Mirepoix", "email": "mr.mirepoix@wanadoo.fr",
                 "phone": "", "website": ""}


@pytest.mark.parametrize("values,expected", [
    (("05 61 68 10 20", "yes"), ("", "05 61 68 10 20")),
    (("contact@ehpad.fr", ""), ("contact@ehpad.fr", "")),
    (("Tél : +33 5 61 68 10 20 / accueil@x.org",), ("accueil@x.org", "+33 5 61 68 10 20")),
    (("yes", "no", None), ("", "")),
])
def test_extract_contacts(values, expected):
    assert prospects.extract_contacts(*values) == expected


def test_get_json_retries_then_raises(monkeypatch):
    class Resp:
        status_code = 503

        def json(self):
            return {}

    attempts = []
    monkeypatch.setattr(prospects.cr, "get", lambda *a, **k: attempts.append(1) or Resp())
    monkeypatch.setattr(prospects.time, "sleep", lambda s: None)
    with pytest.raises(prospects.ProspectError):
        prospects.get_json("/api/v2/search", {})
    assert len(attempts) == 3
```

- [ ] **Step 3: Lancer les tests pour vérifier l'échec**

Run: `python3 -m pytest tests/test_prospects.py -q`
Expected: FAIL — `ImportError: cannot import name 'prospects' from 'jobbot.features'`

- [ ] **Step 4: Implémenter le client**

`jobbot/features/prospects.py` :

```python
"""
Marché caché : établissements qui vont probablement recruter des aides-soignants.

Source : La Bonne Boîte (France Travail) — prédiction des embauches des 3 prochains mois à partir
des 12 derniers mois. API publique du site, sans clé. 7 employeurs sur 10 lisent les candidatures spontanées.
"""

from __future__ import annotations

import re
import threading
import time

from curl_cffi import requests as cr

LBB = "https://labonneboite.francetravail.fr"
ROME_AIDE_SOIGNANT = "J1501"
TIMEOUT = 20
RETRIES = 3

_lock = threading.Lock()  # une requête à la fois vers La Bonne Boîte
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?:\+33\s?|0)[1-9](?:[\s.-]?\d{2}){4}")


class ProspectError(Exception):
    """Échec d'appel à La Bonne Boîte."""


def get_json(path: str, params: dict | None = None) -> dict:
    last = ""
    for attempt in range(RETRIES):
        try:
            with _lock:
                r = cr.get(LBB + path, params=params, impersonate="chrome", timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:  # noqa: BLE001 — erreurs réseau curl_cffi
            last = str(e)[:120]
        if attempt < RETRIES - 1:
            time.sleep(1.5 * 2 ** attempt)
    raise ProspectError(f"La Bonne Boîte {path} : {last}")


def search_establishments(
    citycode: str, distance_km: int, rome: str = ROME_AIDE_SOIGNANT, page_size: int = 50, max_pages: int = 5,
) -> list[dict]:
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        data = get_json("/api/v2/search", {
            "rome": rome, "citycode": citycode, "distance": distance_km, "page": page, "page_size": page_size,
            "sort_by": "romes.hiring_potential", "sort_direction": "desc",
        })
        items = data.get("items") or []
        for it in items:
            loc = it.get("location") or {}
            lo, hi = it.get("headcount_min"), it.get("headcount_max")
            name = it.get("company_name") or it.get("office_name") or "?"
            if it.get("office_name") and it.get("company_name"):
                name = f"{it['company_name']} — {it['office_name']}"
            out.append({
                "siret": str(it.get("siret") or ""), "name": name,
                "naf": it.get("naf") or "", "naf_label": it.get("naf_label") or "",
                "city": it.get("city") or "", "citycode": it.get("citycode") or "", "postcode": it.get("postcode") or "",
                "lat": loc.get("lat"), "lon": loc.get("lon"),
                "headcount": f"{lo}-{hi}" if lo is not None and hi is not None else "",
                "hiring_potential": round(float(it.get("hiring_potential") or 0), 1),
                "high_potential": bool(it.get("is_high_potential")),
            })
        if len(items) < page_size:
            break
    return [o for o in out if o["siret"]]


def extract_contacts(*values: str | None) -> tuple[str, str]:
    """(email, téléphone) trouvés dans des champs mal rangés (l'API met parfois l'e-mail dans « phone »)."""
    text = " ".join(v for v in values if v and v not in ("yes", "no"))
    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    return (email.group(0) if email else "", phone.group(0) if phone else "")


def company_details(siret: str) -> dict:
    d = get_json(f"/api/v2/company/siret/{siret}")
    street = " ".join(x for x in (d.get("streetnumber"), d.get("street")) if x)
    town = " ".join(x for x in (d.get("postcode"), d.get("city")) if x)
    email, phone = extract_contacts(d.get("email"), d.get("phone"), d.get("website"))
    website = d.get("website") or ""
    return {
        "address": ", ".join(x for x in (street, town) if x),
        "email": email,
        "phone": phone,
        "website": "" if EMAIL_RE.search(website) else website,
    }
```

- [ ] **Step 5: Lancer les tests**

Run: `python3 -m pytest tests/test_prospects.py -q`
Expected: PASS (6 tests)

- [ ] **Step 6: Vérifier en réel (une fois, manuellement)**

Run: `python3 -c "from jobbot.features import prospects as p; r=p.search_establishments('09194', 10); print(len(r), r[0]['name'], r[0]['hiring_potential']); print(p.company_details(r[0]['siret']))"`
Expected: au moins 1 établissement (ex. `EHPAD DE MIREPOIX 83.6`) et un dict d'adresse. Si `page_size=50` renvoie une erreur 422, remplacer la valeur par défaut par `20` dans `search_establishments` et dans le test `test_search_stops_when_page_not_full` (`page_size=20`).

- [ ] **Step 7: Commit**

```bash
ruff check . && git add jobbot/features/prospects.py tests/fixtures/lbb_search.json tests/fixtures/lbb_company.json tests/test_prospects.py
git commit -m "feat(prospects): client La Bonne Boîte sans clé (recherche, détail, contacts)"
```

---

### Task 2: Stockage des établissements, distance et rapprochement avec les offres

**Files:**
- Modify: `jobbot/features/prospects.py`
- Modify: `tests/test_prospects.py`

**Interfaces:**
- Consumes: `extensions.schema`, `db.connect`, `db.list_offers`, `geo.haversine_km((lon, lat), (lon, lat))`, `intel.norm_company`, `intel.norm_city`, `rapidfuzz.fuzz.token_set_ratio`
- Produces:
  - table `prospects` (voir SQL)
  - `PROSPECT_STATUSES = ("", "a_contacter", "envoyee", "relance", "entretien", "refus", "ignore")`
  - `upsert_prospects(items: list[dict], hub: str, center: tuple[float, float] | None, path=None) -> int` (nombre inséré ou mis à jour ; garde la plus petite distance et le hub correspondant)
  - `save_details(siret: str, details: dict, path=None) -> None`
  - `sirets_missing_details(limit: int, path=None) -> list[str]` (par potentiel décroissant, statut ≠ `ignore`)
  - `list_prospects(path=None) -> list[dict]` ; chaque dict = colonnes de la table + `high_potential: bool` + `active_offers: list[{id, title, url}]` + `lbb_url: str`
  - `update_prospect(siret: str, status: str | None = None, note: str | None = None, path=None) -> dict | None` (lève `ValueError` si statut inconnu)

- [ ] **Step 1: Écrire les tests qui échouent** (ajouter à `tests/test_prospects.py`)

```python
from jobbot import db


def sample_items(fake=None):
    return prospects.search_establishments("09225", 10, page_size=50)


def test_upsert_keeps_closest_hub_and_status(fake_lbb, tmp_db):
    items = sample_items()
    assert prospects.upsert_prospects(items, "Pamiers", (1.6079, 43.1165)) == 3
    prospects.update_prospect("26090012100013", status="envoyee", note="Envoyé par mail")
    # même établissement vu depuis Mirepoix (plus proche) : distance et hub mis à jour, statut conservé
    prospects.upsert_prospects(items[:1], "Mirepoix", (1.8707, 43.1137))
    p = next(x for x in prospects.list_prospects() if x["siret"] == "26090012100013")
    assert p["hub"] == "Mirepoix" and p["distance_km"] < 5  # ≈ 2,8 km (contre ≈ 21 km depuis Pamiers)
    assert p["status"] == "envoyee" and p["note"] == "Envoyé par mail"
    assert p["lbb_url"] == "https://labonneboite.francetravail.fr/entreprise/26090012100013"


def test_list_orders_by_potential_and_puts_ignored_last(fake_lbb, tmp_db):
    prospects.upsert_prospects(sample_items(), "Pamiers", (1.6079, 43.1165))
    prospects.update_prospect("77553012300019", status="ignore")
    order = [p["siret"] for p in prospects.list_prospects()]
    assert order == ["26090012100013", "41234567800012", "77553012300019"]


def test_details_saved_and_missing_list(fake_lbb, tmp_db):
    prospects.upsert_prospects(sample_items(), "Pamiers", (1.6079, 43.1165))
    assert prospects.sirets_missing_details(2) == ["77553012300019", "26090012100013"]
    prospects.save_details("26090012100013", prospects.company_details("26090012100013"))
    assert "26090012100013" not in prospects.sirets_missing_details(10)
    p = next(x for x in prospects.list_prospects() if x["siret"] == "26090012100013")
    assert p["email"] == "mr.mirepoix@wanadoo.fr" and p["address"].startswith("22 rue")


def test_active_offers_linked_by_employer_and_city(fake_lbb, tmp_db):
    prospects.upsert_prospects(sample_items(), "Pamiers", (1.6079, 43.1165))
    run = db.start_run({})
    db.ingest([
        {"apply_url": "https://ft/1", "source": "France Travail", "title": "Aide-soignant (H/F)", "company": "Ehpad de Mirepoix",
         "location": "09 - MIREPOIX", "search_hub": "Mirepoix"},
        {"apply_url": "https://ft/2", "source": "France Travail", "title": "Aide-soignant", "company": "Ehpad de Mirepoix",
         "location": "31 - TOULOUSE", "search_hub": "Toulouse"},
    ], run)
    p = next(x for x in prospects.list_prospects() if x["siret"] == "26090012100013")
    assert [o["url"] for o in p["active_offers"]] == ["https://ft/1"]


def test_update_prospect_rejects_unknown_status(fake_lbb, tmp_db):
    prospects.upsert_prospects(sample_items(), "Pamiers", None)
    with pytest.raises(ValueError):
        prospects.update_prospect("26090012100013", status="nimporte")
    assert prospects.update_prospect("00000000000000", status="envoyee") is None
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `python3 -m pytest tests/test_prospects.py -q`
Expected: FAIL — `AttributeError: module 'jobbot.features.prospects' has no attribute 'upsert_prospects'` (ou `no such table: prospects`)

- [ ] **Step 3: Implémenter le stockage** (ajouter à `jobbot/features/prospects.py`)

Ajouter aux imports :

```python
from datetime import datetime

from rapidfuzz import fuzz

from jobbot import db, extensions, geo, intel
```

Ajouter après les constantes :

```python
PROSPECT_STATUSES = ("", "a_contacter", "envoyee", "relance", "entretien", "refus", "ignore")

extensions.schema("""
CREATE TABLE IF NOT EXISTS prospects (
  siret TEXT PRIMARY KEY, name TEXT NOT NULL, naf TEXT DEFAULT '', naf_label TEXT DEFAULT '',
  city TEXT DEFAULT '', citycode TEXT DEFAULT '', postcode TEXT DEFAULT '', lat REAL, lon REAL,
  headcount TEXT DEFAULT '', hiring_potential REAL DEFAULT 0, high_potential INTEGER DEFAULT 0,
  hub TEXT DEFAULT '', distance_km REAL,
  address TEXT DEFAULT '', email TEXT DEFAULT '', phone TEXT DEFAULT '', website TEXT DEFAULT '', details_at TEXT,
  first_seen TEXT, last_seen TEXT, status TEXT DEFAULT '', status_at TEXT, note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_prospects_potential ON prospects(hiring_potential DESC);
""")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def upsert_prospects(items: list[dict], hub: str, center: tuple[float, float] | None, path=None) -> int:
    now = _now()
    with db.connect(path) as c:
        for it in items:
            dist = None
            if center and it.get("lat") is not None and it.get("lon") is not None:
                dist = round(geo.haversine_km((it["lon"], it["lat"]), center), 1)
            c.execute(
                """INSERT INTO prospects (siret, name, naf, naf_label, city, citycode, postcode, lat, lon, headcount,
                     hiring_potential, high_potential, hub, distance_km, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(siret) DO UPDATE SET
                     name=excluded.name, naf=excluded.naf, naf_label=excluded.naf_label, city=excluded.city,
                     citycode=excluded.citycode, postcode=excluded.postcode, lat=excluded.lat, lon=excluded.lon,
                     headcount=excluded.headcount, hiring_potential=excluded.hiring_potential,
                     high_potential=excluded.high_potential, last_seen=excluded.last_seen,
                     hub=CASE WHEN prospects.distance_km IS NULL OR (excluded.distance_km IS NOT NULL
                              AND excluded.distance_km < prospects.distance_km) THEN excluded.hub ELSE prospects.hub END,
                     distance_km=CASE WHEN prospects.distance_km IS NULL OR (excluded.distance_km IS NOT NULL
                              AND excluded.distance_km < prospects.distance_km) THEN excluded.distance_km
                              ELSE prospects.distance_km END""",
                (it["siret"], it["name"], it["naf"], it["naf_label"], it["city"], it["citycode"], it["postcode"],
                 it["lat"], it["lon"], it["headcount"], it["hiring_potential"], int(it["high_potential"]),
                 hub, dist, now, now),
            )
    return len(items)


def save_details(siret: str, details: dict, path=None) -> None:
    with db.connect(path) as c:
        c.execute("UPDATE prospects SET address=?, email=?, phone=?, website=?, details_at=? WHERE siret=?",
                  (details.get("address", ""), details.get("email", ""), details.get("phone", ""),
                   details.get("website", ""), _now(), siret))


def sirets_missing_details(limit: int, path=None) -> list[str]:
    with db.connect(path) as c:
        rows = c.execute("SELECT siret FROM prospects WHERE details_at IS NULL AND status != 'ignore' "
                         "ORDER BY hiring_potential DESC LIMIT ?", (limit,)).fetchall()
    return [r["siret"] for r in rows]


def _link_offers(prospects_list: list[dict], offers: list[dict]) -> None:
    by_city: dict[str, list[dict]] = {}
    for o in offers:
        if intel.norm_company(o["company"]):
            by_city.setdefault(intel.norm_city(o["location"]) or o["city_norm"], []).append(o)
    for p in prospects_list:
        name = intel.norm_company(p["name"])
        candidates = by_city.get(intel.norm_city(p["city"]), [])
        p["active_offers"] = [
            {"id": o["id"], "title": o["title"], "url": o["url"]}
            for o in candidates
            if name and fuzz.token_set_ratio(name, intel.norm_company(o["company"])) >= 85
        ]


def list_prospects(path=None) -> list[dict]:
    with db.connect(path) as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM prospects ORDER BY status = 'ignore', hiring_potential DESC, name")]
    for p in rows:
        p["high_potential"] = bool(p["high_potential"])
        p["lbb_url"] = f"{LBB}/entreprise/{p['siret']}"
    _link_offers(rows, db.list_offers(include_inactive=False, path=path))
    return rows


def update_prospect(siret: str, status: str | None = None, note: str | None = None, path=None) -> dict | None:
    if status is not None and status not in PROSPECT_STATUSES:
        raise ValueError(f"statut inconnu : {status}")
    with db.connect(path) as c:
        if not c.execute("SELECT 1 FROM prospects WHERE siret = ?", (siret,)).fetchone():
            return None
        if status is not None:
            c.execute("UPDATE prospects SET status=?, status_at=? WHERE siret=?", (status, _now(), siret))
        if note is not None:
            c.execute("UPDATE prospects SET note=? WHERE siret=?", (str(note)[:4000], siret))
    return next((p for p in list_prospects(path) if p["siret"] == siret), None)
```

Remarque : la fixture `tmp_db` appelle `db.init()` **après** l'import du module (le schéma est enregistré à l'import de `prospects`, fait en tête du fichier de test) : la table existe donc bien.

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest tests/test_prospects.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
ruff check . && git add jobbot/features/prospects.py tests/test_prospects.py
git commit -m "feat(prospects): stockage, distance, suivi et offres en ligne liées"
```

---

### Task 3: Actualisation, routes API et crochet post-recherche

**Files:**
- Modify: `jobbot/features/prospects.py`
- Modify: `tests/test_prospects.py`

**Interfaces:**
- Consumes: `extensions.route`, `extensions.post_run`, `scraper.normalize_config`, `scraper.radius_for`, `sources.resolve_insee`, `geo.geocode_insee`, `server.load_config` (import local dans le handler)
- Produces:
  - `refresh_prospects(cfg: dict, log: Callable[[str], None] = print, details_limit: int = 40, path=None) -> dict` → `{"found": int, "details": int, "errors": list[str]}`
  - `GET /api/prospects` → `200, list_prospects()`
  - `POST /api/prospects/<siret 14 chiffres>` corps `{status?, note?}` → `200, prospect` | `400 {"error"}` | `404 {"error"}`
  - `POST /api/prospects/refresh` → `200, refresh_prospects(load_config())`
  - crochet post-recherche : `refresh_prospects(cfg, log, details_limit=20)`

- [ ] **Step 1: Écrire les tests qui échouent** (ajouter à `tests/test_prospects.py`)

```python
from jobbot import extensions


def test_refresh_prospects_uses_cities_and_radius(fake_lbb, tmp_db, monkeypatch):
    monkeypatch.setattr(prospects, "resolve_insee", lambda city: {"Pamiers": "09225", "Mirepoix": "09194"}[city])
    monkeypatch.setattr(prospects.geo, "geocode_insee", lambda code: (1.6, 43.1))
    cfg = {"cities": ["Pamiers", "Mirepoix"], "radius_km": 10, "city_radius": {"Mirepoix": 15}}
    logs = []
    res = prospects.refresh_prospects(cfg, logs.append, details_limit=1)
    searches = [p for path, p in fake_lbb if path == "/api/v2/search"]
    assert [(s["citycode"], s["distance"]) for s in searches] == [("09225", 10), ("09194", 15)]
    assert res == {"found": 3, "details": 1, "errors": []}
    assert any("Établissements" in line for line in logs)


def test_refresh_reports_errors_without_raising(tmp_db, monkeypatch):
    def boom(path, params=None):
        raise prospects.ProspectError("HTTP 503")

    monkeypatch.setattr(prospects, "get_json", boom)
    monkeypatch.setattr(prospects, "resolve_insee", lambda city: "09225")
    monkeypatch.setattr(prospects.geo, "geocode_insee", lambda code: None)
    res = prospects.refresh_prospects({"cities": ["Pamiers"], "radius_km": 10, "city_radius": {}}, lambda m: None)
    assert res == {"found": 0, "details": 0, "errors": ["Pamiers : HTTP 503"]}


def test_routes(fake_lbb, tmp_db):
    prospects.upsert_prospects(sample_items(), "Pamiers", None)
    route, m = extensions.find_route("GET", "/api/prospects")
    status, payload = route.handler(None, m, {}, {})
    assert status == 200 and len(payload) == 3
    route, m = extensions.find_route("POST", "/api/prospects/26090012100013")
    assert route.handler(None, m, {}, {"status": "envoyee"})[1]["status"] == "envoyee"
    assert route.handler(None, m, {}, {"status": "bad"})[0] == 400
    route, m = extensions.find_route("POST", "/api/prospects/99999999999999")
    assert route.handler(None, m, {}, {"status": "envoyee"})[0] == 404


def test_post_run_hook_registered():
    assert prospects.after_run in extensions.POST_RUN_HOOKS
```

- [ ] **Step 2: Lancer pour vérifier l'échec**

Run: `python3 -m pytest tests/test_prospects.py -q`
Expected: FAIL — `AttributeError: … has no attribute 'refresh_prospects'`

- [ ] **Step 3: Implémenter** (ajouter à `jobbot/features/prospects.py`)

Ajouter aux imports :

```python
from collections.abc import Callable

from jobbot.scraper import normalize_config, radius_for
from jobbot.sources import resolve_insee
```

Ajouter en fin de fichier :

```python
def refresh_prospects(cfg: dict, log: Callable[[str], None] = print, details_limit: int = 40, path=None) -> dict:
    cfg = normalize_config(cfg)  # idempotente : accepte une config partielle ou déjà normalisée
    found, errors = 0, []
    for city in cfg["cities"]:
        code = resolve_insee(city)
        if not code:
            errors.append(f"{city} : commune introuvable")
            continue
        try:
            items = search_establishments(code, radius_for(cfg, city))
        except ProspectError as e:
            errors.append(f"{city} : {e}")
            continue
        found += upsert_prospects(items, city, geo.geocode_insee(code), path=path)
    details = 0
    for siret in sirets_missing_details(details_limit, path=path):
        try:
            save_details(siret, company_details(siret), path=path)
            details += 1
        except ProspectError as e:
            errors.append(f"{siret} : {e}")
            break
    log(f"Établissements (La Bonne Boîte) : {found} trouvés, {details} fiches complétées"
        + (f", {len(errors)} erreur(s)" if errors else ""))
    return {"found": found, "details": details, "errors": errors}


@extensions.post_run
def after_run(cfg: dict, summary: dict, log: Callable[[str], None]) -> None:
    refresh_prospects(cfg, log, details_limit=20)


@extensions.route("GET", r"/api/prospects")
def _route_list(req, match, query, body):
    return 200, list_prospects()


@extensions.route("POST", r"/api/prospects/refresh")
def _route_refresh(req, match, query, body):
    from jobbot.server import load_config

    return 200, refresh_prospects(load_config())


@extensions.route("POST", r"/api/prospects/(\d{14})")
def _route_update(req, match, query, body):
    try:
        prospect = update_prospect(match.group(1), body.get("status"), body.get("note"))
    except ValueError as e:
        return 400, {"error": str(e)}
    return (200, prospect) if prospect else (404, {"error": "établissement introuvable"})
```

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff check . && git add jobbot/features/prospects.py tests/test_prospects.py
git commit -m "feat(prospects): actualisation par ville, routes API et crochet après recherche"
```

---

### Task 4: Vue « Établissements » et carte dans la vue d'ensemble

**Files:**
- Create: `jobbot/web/features/prospects.js`
- Modify: `README.md` (section Fonctionnalités)
- Modify: `progress/JOURNAL.md`

**Interfaces:**
- Consumes: `window.JobBot.{registerView, hooks.overviewCards, hooks.afterLoad, api, esc, icon, fmtInt, km, go, toast, S}` ; routes Task 3 ; si présent (plan 04) `window.JobBot.openLetter("spontanee", { siret })`
- Produces: vue `#/etablissements`, carte « Établissements à démarcher »

- [ ] **Step 1: Écrire la vue**

`jobbot/web/features/prospects.js` :

```javascript
/* Marché caché : établissements qui recrutent (La Bonne Boîte) */
(() => {
  const J = window.JobBot;
  const LABELS = { "": "À évaluer", a_contacter: "À contacter", envoyee: "Candidature envoyée", relance: "Relancé",
    entretien: "Entretien", refus: "Refus", ignore: "Ignoré" };
  const state = { items: [], q: "", status: "", loading: false };

  async function load() {
    try { state.items = await J.api("/api/prospects"); } catch { state.items = []; }
  }
  J.hooks.afterLoad.push(load);

  const potentialBadge = (p) => `<span class="score ${p.hiring_potential >= 70 ? "hi" : p.hiring_potential >= 40 ? "mid" : ""}"
    title="Probabilité d'embauche dans les 3 mois (La Bonne Boîte)">${Math.round(p.hiring_potential)}</span>`;

  function row(p) {
    const contact = [
      p.phone ? `<a href="tel:${J.esc(p.phone.replace(/\s/g, ""))}">${J.esc(p.phone)}</a>` : "",
      p.email ? `<a href="mailto:${J.esc(p.email)}">${J.esc(p.email)}</a>` : "",
      p.address ? `<span class="muted">${J.esc(p.address)}</span>` : "",
    ].filter(Boolean).join("<br/>") || `<span class="muted">—</span>`;
    const offers = p.active_offers.length
      ? `<button type="button" class="badge b-ok" data-go="offres" data-params='${J.esc(JSON.stringify({ q: p.name }))}'>${p.active_offers.length} offre${p.active_offers.length > 1 ? "s" : ""} en ligne</button>`
      : `<span class="muted" style="font-size:.8125rem">Aucune : candidature spontanée</span>`;
    const letter = typeof J.openLetter === "function"
      ? `<button type="button" class="btn-ghost btn-sm" data-letter-siret="${p.siret}">Rédiger</button>` : "";
    return `<tr class="row ${p.status === "ignore" ? "dim" : ""}">
      <td>${potentialBadge(p)}</td>
      <td><div class="cell-title"><strong>${J.esc(p.name)}</strong><span class="co">${J.esc(p.naf_label)}${p.headcount ? ` · ${J.esc(p.headcount)} salariés` : ""}</span></div></td>
      <td>${J.esc(p.city)}<br/><span class="muted num">${J.km(p.distance_km)} de ${J.esc(p.hub)}</span></td>
      <td style="font-size:.8125rem">${contact}</td>
      <td>${offers}</td>
      <td><label class="sr-only" for="ps-${p.siret}">Suivi</label>
        <select class="status" id="ps-${p.siret}" data-siret="${p.siret}">${Object.entries(LABELS).map(([k, v]) => `<option value="${k}" ${k === p.status ? "selected" : ""}>${v}</option>`).join("")}</select>
        <label class="sr-only" for="pn-${p.siret}">Note</label>
        <textarea class="pnote" id="pn-${p.siret}" data-siret="${p.siret}" placeholder="Note…" style="min-height:40px;margin-top:6px">${J.esc(p.note)}</textarea></td>
      <td style="white-space:nowrap">${letter}
        <a class="btn btn-ghost btn-sm icon-btn" href="${J.esc(p.lbb_url)}" target="_blank" rel="noopener" aria-label="Voir sur La Bonne Boîte">${J.icon("ext")}</a></td>
    </tr>`;
  }

  function render(section) {
    const q = J.fold(state.q);
    const list = state.items.filter(p => (!q || J.fold(`${p.name} ${p.city} ${p.naf_label}`).includes(q)) && (!state.status || p.status === state.status));
    const high = state.items.filter(p => p.high_potential);
    const contacted = state.items.filter(p => ["envoyee", "relance", "entretien"].includes(p.status));
    section.innerHTML = `
      <h2 class="view-title">Établissements</h2>
      <p class="view-sub">Le marché caché : établissements susceptibles de recruter des aides-soignants dans les 3 mois (La Bonne Boîte, France Travail), même sans offre publiée. 7 employeurs sur 10 lisent les candidatures spontanées.</p>
      <div class="kpis">
        <div class="kpi"><span class="label">Établissements</span><span class="value">${J.fmtInt(state.items.length)}</span><span class="hint">dans ton périmètre</span></div>
        <div class="kpi"><span class="label">Fort potentiel</span><span class="value">${J.fmtInt(high.length)}</span><span class="hint">${high.filter(p => !p.status).length} pas encore contactés</span></div>
        <div class="kpi"><span class="label">Contactés</span><span class="value">${J.fmtInt(contacted.length)}</span><span class="hint">${state.items.filter(p => p.status === "entretien").length} entretiens</span></div>
      </div>
      <div class="filterbar">
        <label class="sr-only" for="pr-q">Rechercher</label><input type="search" id="pr-q" placeholder="Rechercher un établissement, une ville…" value="${J.esc(state.q)}"/>
        <label class="sr-only" for="pr-status">Suivi</label>
        <select id="pr-status"><option value="">Tous les suivis</option>${Object.entries(LABELS).map(([k, v]) => `<option value="${k}" ${k === state.status && state.status ? "selected" : ""}>${v}</option>`).join("")}</select>
        <button type="button" class="btn-ghost btn-sm" id="pr-refresh" ${state.loading ? "disabled" : ""}>${state.loading ? "Actualisation…" : "Actualiser"}</button>
      </div>
      <div class="table-wrap"><table class="data"><thead><tr>
        <th class="static">Potentiel</th><th class="static">Établissement</th><th class="static">Lieu</th><th class="static">Contact</th>
        <th class="static">Offres</th><th class="static">Suivi</th><th class="static"></th></tr></thead>
        <tbody>${list.map(row).join("") || `<tr><td colspan="7"><div class="empty"><strong>Aucun établissement</strong>Clique sur « Actualiser » ou lance une recherche.</div></td></tr>`}</tbody></table></div>`;
  }

  function bind(section) {
    section.addEventListener("input", (e) => {
      if (e.target.id === "pr-q") { state.q = e.target.value; const pos = e.target.selectionStart; render(section); const el = section.querySelector("#pr-q"); el.focus(); el.setSelectionRange(pos, pos); }
    });
    section.addEventListener("change", async (e) => {
      if (e.target.id === "pr-status") { state.status = e.target.value; render(section); }
      if (e.target.matches("select.status")) {
        const p = await J.api(`/api/prospects/${e.target.dataset.siret}`, { status: e.target.value });
        state.items = state.items.map(x => x.siret === p.siret ? p : x);
        J.toast(`Suivi : ${LABELS[p.status]}`); render(section); J.renderView();
      }
    });
    section.addEventListener("focusout", async (e) => {
      if (!e.target.matches("textarea.pnote")) return;
      const p = state.items.find(x => x.siret === e.target.dataset.siret);
      if (p && p.note !== e.target.value) { await J.api(`/api/prospects/${p.siret}`, { note: e.target.value }); p.note = e.target.value; J.toast("Note enregistrée"); }
    });
    section.addEventListener("click", async (e) => {
      if (e.target.id === "pr-refresh") {
        state.loading = true; render(section);
        try { const r = await J.api("/api/prospects/refresh", {}); J.toast(`${r.found} établissements, ${r.details} fiches complétées`); await load(); }
        catch (err) { J.toast("Actualisation impossible : " + err.message); }
        state.loading = false; render(section);
      }
      const letter = e.target.closest("[data-letter-siret]");
      if (letter && typeof J.openLetter === "function") J.openLetter("spontanee", { siret: letter.dataset.letterSiret });
    });
  }

  J.registerView({
    id: "etablissements", label: "Établissements", render, bind,
    count: () => state.items.filter(p => p.high_potential && !p.status).length,
  });

  J.hooks.overviewCards.push((container) => {
    const top = state.items.filter(p => p.high_potential && !p.status).slice(0, 5);
    container.insertAdjacentHTML("beforeend", `<div class="card">
      <div class="card-head"><div><h3>Établissements à démarcher</h3><p class="card-sub">Fort potentiel d'embauche, pas encore contactés</p></div>
        <a class="btn btn-ghost btn-sm" href="#/etablissements">Tout voir</a></div>
      <ul class="todo">${top.map(p => `<li><div class="t"><strong>${J.esc(p.name)}</strong><span>${J.esc(p.city)} · ${J.km(p.distance_km)} · ${p.active_offers.length ? "offre en ligne" : "candidature spontanée"}</span></div>
        <span class="score ${p.hiring_potential >= 70 ? "hi" : "mid"}">${Math.round(p.hiring_potential)}</span></li>`).join("")
        || `<li class="empty" style="display:block">Aucun établissement à fort potentiel non contacté.</li>`}</ul></div>`);
  });
})();
```

- [ ] **Step 2: Vérifier dans le navigateur**

Run: `python3 -m jobbot serve --no-browser`, ouvrir `http://127.0.0.1:8765/#/etablissements`, cliquer « Actualiser ».
Expected: l'onglet « Établissements » apparaît avant « Recherche » ; après actualisation, la liste contient l'EHPAD de Mirepoix (potentiel ≈ 84) ; changer le suivi affiche un toast et persiste après rechargement ; aucune erreur dans la console. Vue d'ensemble : carte « Établissements à démarcher ». Vérifier aussi à 375 px de large (pas de défilement horizontal de la page).

- [ ] **Step 3: Documenter**

Dans `README.md`, section « Fonctionnalités », ajouter après la puce *Doublons* :

```markdown
  - *Établissements* : le **marché caché** — établissements susceptibles de recruter dans les 3 mois (La Bonne Boîte), avec coordonnées, distance, offres en ligne liées et suivi des candidatures spontanées.
```

Dans `progress/JOURNAL.md`, ajouter la ligne :

```markdown
| 2026-09-17 | Plan 01 — Marché caché (La Bonne Boîte) | ✅ |
```

- [ ] **Step 4: Tests, lint, commit**

```bash
python3 -m pytest -q && ruff check .
git add jobbot/web/features/prospects.js README.md progress/JOURNAL.md
git commit -m "feat(prospects): vue Établissements et carte « à démarcher »"
```
