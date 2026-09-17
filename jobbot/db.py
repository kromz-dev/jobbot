"""
JobBot - Base de données SQLite

Modèle :
  offers          un poste réel (dédoublonné), avec signaux, score, cycle de vie et suivi
  listings        une annonce sur un site (URL) → rattachée à une offre
  runs            une recherche (config, compteurs, santé des sources)
  status_history  chaque changement de statut
  dedup_blocks    « ces deux annonces ne sont PAS la même offre » (séparation manuelle)

Cycle de vie : une offre non revue lors de 2 recherches consécutives (sur des sources
qui ont fonctionné) passe « disparue ». Si elle revient → « republiée ».
"""

from __future__ import annotations

import csv
import functools
import json
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from . import extensions, intel
from .paths import DB_PATH

MISSES_BEFORE_CLOSED = 2
MATCH_WINDOW_DAYS = 60

_write_lock = threading.RLock()   # sérialise TOUT accès (lecture comme écriture)
_connections: dict[str, sqlite3.Connection] = {}
TRANSIENT_ERRORS = ("disk i/o error", "database is locked", "unable to open database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS offers (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL, company TEXT DEFAULT '', location TEXT DEFAULT '', hub TEXT DEFAULT '',
  city_norm TEXT DEFAULT '', distance_km REAL,
  contract TEXT DEFAULT '', contract_raw TEXT DEFAULT '', shift TEXT DEFAULT '', part_time INTEGER DEFAULT 0,
  salary_text TEXT DEFAULT '', salary_min INTEGER, salary_max INTEGER,
  description TEXT DEFAULT '', enriched INTEGER DEFAULT 0, enrich_attempts INTEGER DEFAULT 0,
  ff INTEGER DEFAULT 0, diploma_required INTEGER DEFAULT 0, beginner_ok INTEGER DEFAULT 0, signals_json TEXT DEFAULT '{}',
  score INTEGER DEFAULT 0, score_reasons TEXT DEFAULT '[]',
  posted_date TEXT DEFAULT '', first_seen TEXT, last_seen TEXT, first_run_id INTEGER, last_run_id INTEGER,
  is_active INTEGER DEFAULT 1, missed_runs INTEGER DEFAULT 0, closed_at TEXT, reposted INTEGER DEFAULT 0,
  status TEXT DEFAULT '', note TEXT DEFAULT '', status_at TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_offers_city ON offers(city_norm);
CREATE INDEX IF NOT EXISTS ix_offers_active ON offers(is_active);

CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY,
  offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
  source TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
  title TEXT DEFAULT '', company TEXT DEFAULT '', location TEXT DEFAULT '', contract_raw TEXT DEFAULT '',
  salary_text TEXT DEFAULT '', description TEXT DEFAULT '', posted_date TEXT DEFAULT '',
  hub TEXT DEFAULT '', distance_km REAL, city_norm TEXT DEFAULT '',
  first_seen TEXT, last_seen TEXT, last_run_id INTEGER,
  match_confidence INTEGER, match_reason TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_listings_offer ON listings(offer_id);
CREATE INDEX IF NOT EXISTS ix_listings_city ON listings(city_norm);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT, finished_at TEXT, status TEXT DEFAULT 'running', trigger TEXT DEFAULT 'manuel',
  config_json TEXT DEFAULT '{}', duration_s INTEGER DEFAULT 0,
  listings INTEGER DEFAULT 0, new_offers INTEGER DEFAULT 0, merged INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
  offers_active INTEGER DEFAULT 0, ff INTEGER DEFAULT 0, closed INTEGER DEFAULT 0, reposted INTEGER DEFAULT 0,
  enriched INTEGER DEFAULT 0, per_source_json TEXT DEFAULT '{}', error TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS status_history (
  id INTEGER PRIMARY KEY, offer_id INTEGER REFERENCES offers(id) ON DELETE CASCADE, status TEXT, at TEXT
);

CREATE TABLE IF NOT EXISTS dedup_blocks (
  url TEXT NOT NULL, offer_id INTEGER NOT NULL, PRIMARY KEY (url, offer_id)
);

CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY, applied_at TEXT);
"""

STATUSES = {"", "vu", "favori", "postule", "relance", "entretien", "offre", "refuse", "masque"}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _open(key: str) -> sqlite3.Connection:
    conn = sqlite3.connect(key, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # TRUNCATE plutôt que DELETE : le journal n'est pas recréé/supprimé à chaque transaction,
    # ce qui évite les "disk I/O error" sur les disques Windows montés dans WSL (/mnt/c) et avec antivirus.
    conn.execute("PRAGMA journal_mode = TRUNCATE")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _reset(path: Path | str | None = None) -> None:
    key = str(path or DB_PATH)
    with _write_lock:
        conn = _connections.pop(key, None)
        if conn:
            try:
                conn.close()
            except sqlite3.Error:
                pass


@contextmanager
def connect(path: Path | str | None = None):
    """Connexion partagée par fichier, une transaction à la fois (verrou réentrant)."""
    key = str(path or DB_PATH)
    with _write_lock:
        conn = _connections.get(key)
        if conn is None:
            conn = _connections[key] = _open(key)
        if conn.in_transaction:  # appel imbriqué : la transaction englobante valide
            yield conn
            return
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def retry_io(func):
    """Rejoue une opération (transaction entière) si SQLite signale une erreur d'E/S passagère."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(5):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if attempt == 4 or not any(t in str(e).lower() for t in TRANSIENT_ERRORS):
                    raise
                _reset(kwargs.get("path"))
                time.sleep(0.3 * 2 ** attempt)
    return wrapper


@retry_io
def init(path: Path | str | None = None) -> None:
    with _write_lock, connect(path) as c:
        c.executescript(SCHEMA)
        for sql in extensions.SCHEMAS:
            c.executescript(sql)
        applied = {r["name"] for r in c.execute("SELECT name FROM migrations")}
        for name, sql in extensions.MIGRATIONS:
            if name not in applied:
                # BEGIN/COMMIT explicites dans le même executescript : l'ALTER et l'enregistrement de la
                # migration sont atomiques (un crash entre les deux ne rejoue jamais un ALTER déjà appliqué).
                c.executescript(f"BEGIN;\n{sql};\nINSERT INTO migrations (name, applied_at) VALUES ('{name}', '{now_iso()}');\nCOMMIT;")


def _days_old(posted: str | None) -> int | None:
    try:
        return max(0, (date.today() - date.fromisoformat(str(posted)[:10])).days)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@retry_io
def start_run(config: dict, trigger: str = "manuel", path=None) -> int:
    safe_config = dict(config)
    safe_config.pop("integrations", None)
    with _write_lock, connect(path) as c:
        cur = c.execute(
            "INSERT INTO runs (started_at, config_json, trigger) VALUES (?, ?, ?)",
            (now_iso(), json.dumps(safe_config, ensure_ascii=False), trigger),
        )
        return int(cur.lastrowid)


@retry_io
def finish_run(run_id: int, path=None, **fields) -> None:
    fields.setdefault("finished_at", now_iso())
    if "per_source" in fields:
        fields["per_source_json"] = json.dumps(fields.pop("per_source"), ensure_ascii=False)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _write_lock, connect(path) as c:
        c.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))


@retry_io
def list_runs(limit: int = 200, path=None) -> list[dict]:
    with connect(path) as c:
        rows = c.execute("SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        config = json.loads(d.pop("config_json") or "{}")
        config.pop("integrations", None)  # jamais de secrets dans l'historique (couvre aussi les anciennes lignes)
        d["config"] = config
        d["per_source"] = json.loads(d.pop("per_source_json") or "{}")
        out.append(d)
    return out


def last_finished_run(path=None) -> dict | None:
    runs = [r for r in list_runs(20, path) if r["finished_at"]]
    return runs[0] if runs else None


# ---------------------------------------------------------------------------
# Ingestion + dédoublonnage
# ---------------------------------------------------------------------------

def _candidate(row: dict | sqlite3.Row) -> intel.Candidate:
    return intel.Candidate(
        title=row["title"], company=row["company"] or "", city=row["city_norm"] or "",
        description=row["description"] or "", contract=intel.contract_kind(row["contract_raw"]),
        shift=intel.shift_kind(row["title"]), source=row["source"],
    )


@retry_io
def ingest(listings: list[dict], run_id: int, path=None) -> dict:
    """Insère/rattache les annonces d'une recherche. Renvoie les compteurs."""
    now = now_iso()
    today = date.today().isoformat()
    stats = {"listings": len(listings), "new_offers": 0, "merged": 0, "updated": 0, "reposted": 0}
    with _write_lock, connect(path) as c:
        known = {r["url"]: dict(r) for r in c.execute(
            "SELECT l.*, o.is_active FROM listings l JOIN offers o ON o.id = l.offer_id "
            "WHERE l.last_seen >= date('now', ?)", (f"-{MATCH_WINDOW_DAYS} days",))}
        by_city: dict[str, list[dict]] = defaultdict(list)
        for row in known.values():
            by_city[row["city_norm"]].append(row)
        blocks = {(r["url"], r["offer_id"]) for r in c.execute("SELECT url, offer_id FROM dedup_blocks")}
        touched: set[int] = set()

        for lst in listings:
            url = lst.get("apply_url") or ""
            if not url:
                continue
            city = intel.norm_city(lst.get("location")) or intel.norm_city(lst.get("search_hub"))
            data = {
                "source": lst.get("source") or "?", "url": url, "title": lst.get("title") or "",
                "company": lst.get("company") or "", "location": lst.get("location") or "",
                "contract_raw": lst.get("contract_type") or "", "salary_text": lst.get("salary") or "",
                "description": lst.get("description") or "", "posted_date": str(lst.get("posted_date") or "")[:10],
                "hub": lst.get("search_hub") or "", "distance_km": lst.get("distance_km"), "city_norm": city,
            }

            existing = known.get(url) or c.execute("SELECT * FROM listings WHERE url = ?", (url,)).fetchone()
            if existing:
                existing = dict(existing)
                desc = data["description"] if len(data["description"]) > len(existing["description"] or "") else existing["description"]
                c.execute(
                    "UPDATE listings SET title=?, company=COALESCE(NULLIF(?, ''), company), location=?, contract_raw=?, "
                    "salary_text=COALESCE(NULLIF(?, ''), salary_text), description=?, "
                    "posted_date=COALESCE(NULLIF(?, ''), posted_date), hub=?, distance_km=?, city_norm=?, "
                    "last_seen=?, last_run_id=? WHERE id=?",
                    (data["title"], data["company"], data["location"], data["contract_raw"], data["salary_text"], desc,
                     data["posted_date"], data["hub"], data["distance_km"], city, now, run_id, existing["id"]),
                )
                touched.add(existing["offer_id"])
                stats["updated"] += 1
                continue

            # Recherche d'une offre existante décrivant le même poste
            new_cand = _candidate(data)
            best: tuple[int, int, str] | None = None  # (confidence, offer_id, reason)
            per_offer: dict[int, list[dict]] = defaultdict(list)
            for other in by_city.get(city, []):
                per_offer[other["offer_id"]].append(other)
            for offer_id, group in per_offer.items():
                if (url, offer_id) in blocks:
                    continue
                matches = [intel.compare(new_cand, _candidate(o)) for o in group]
                # Une offre qui a déjà une annonce du même site doit la reconnaître comme identique
                same_site = [m for o, m in zip(group, matches, strict=True) if o["source"] == data["source"]]
                if same_site and not all(m.same for m in same_site):
                    continue
                good = [m for m in matches if m.same]
                if not good:
                    continue
                top = max(good, key=lambda m: m.confidence)
                if best is None or top.confidence > best[0]:
                    best = (top.confidence, offer_id, top.reason)

            if best:
                offer_id = best[1]
                stats["merged"] += 1
            else:
                cur = c.execute(
                    "INSERT INTO offers (title, company, location, hub, city_norm, first_seen, last_seen, "
                    "first_run_id, last_run_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (data["title"], data["company"], data["location"], data["hub"], city, today, today,
                     run_id, run_id, now, now),
                )
                offer_id = int(cur.lastrowid)
                stats["new_offers"] += 1

            cur = c.execute(
                "INSERT INTO listings (offer_id, source, url, title, company, location, contract_raw, salary_text, "
                "description, posted_date, hub, distance_km, city_norm, first_seen, last_seen, last_run_id, "
                "match_confidence, match_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (offer_id, data["source"], url, data["title"], data["company"], data["location"], data["contract_raw"],
                 data["salary_text"], data["description"], data["posted_date"], data["hub"], data["distance_km"], city,
                 now, now, run_id, best[0] if best else None, best[2] if best else ""),
            )
            row = {**data, "id": cur.lastrowid, "offer_id": offer_id, "is_active": 1}
            known[url] = row
            by_city[city].append(row)
            touched.add(offer_id)

        # Offres revues : réactivation / republication
        for offer_id in touched:
            o = c.execute("SELECT is_active, closed_at FROM offers WHERE id = ?", (offer_id,)).fetchone()
            reposted = 1 if o and not o["is_active"] and o["closed_at"] else 0
            stats["reposted"] += reposted
            c.execute(
                "UPDATE offers SET last_seen=?, last_run_id=?, is_active=1, missed_runs=0, closed_at=NULL, "
                "reposted=reposted+?, updated_at=? WHERE id=?",
                (today, run_id, reposted, now, offer_id),
            )
        for offer_id in touched:
            _refresh(c, offer_id)
    stats["touched"] = len(touched)
    return stats


def _refresh(c: sqlite3.Connection, offer_id: int) -> None:
    """Recalcule les champs agrégés, signaux et score d'une offre à partir de ses annonces."""
    rows = [dict(r) for r in c.execute("SELECT * FROM listings WHERE offer_id = ?", (offer_id,))]
    if not rows:
        c.execute("DELETE FROM offers WHERE id = ?", (offer_id,))
        return
    o = dict(c.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone())
    richest = max(rows, key=lambda r: len(r["description"] or ""))
    closest = min(rows, key=lambda r: r["distance_km"] if r["distance_km"] is not None else 9999)
    company = max((r["company"] for r in rows), key=lambda x: (bool(intel.norm_company(x)), len(x or "")))
    salary = next((r["salary_text"] for r in rows if r["salary_text"]), "")
    contract_raw = next((r["contract_raw"] for r in rows if intel.contract_kind(r["contract_raw"])), rows[0]["contract_raw"])
    posted = min((r["posted_date"] for r in rows if r["posted_date"]), default="")
    description = richest["description"] or ""
    title = richest["title"] or rows[0]["title"]

    # Signaux sur TOUS les titres (un site écrit « Aide-soignant DE », un autre « Aide-Soignant de »)
    all_titles = " | ".join(dict.fromkeys(r["title"] for r in rows if r["title"]))
    sig = intel.extract_signals(all_titles, description, contract_raw)
    sal_min, sal_max = intel.parse_salary(salary)
    sources = {r["source"] for r in rows}
    score, reasons = intel.score_offer(
        sig, title, _days_old(posted), closest["distance_km"], len(sources), o["reposted"], sal_max,
    )
    c.execute(
        "UPDATE offers SET title=?, company=?, location=?, hub=?, city_norm=?, distance_km=?, contract=?, contract_raw=?, "
        "shift=?, part_time=?, salary_text=?, salary_min=?, salary_max=?, description=?, ff=?, diploma_required=?, "
        "beginner_ok=?, signals_json=?, score=?, score_reasons=?, posted_date=? WHERE id=?",
        (title, company or "", closest["location"], closest["hub"], closest["city_norm"], closest["distance_km"],
         sig.contract, contract_raw or "", sig.shift, int(sig.part_time), salary, sal_min, sal_max, description,
         int(sig.ff), int(sig.diploma_required), int(sig.beginner_ok),
         json.dumps({"ff": sig.ff_match, "diploma": sig.diploma_match, "beginner": sig.beginner_match}, ensure_ascii=False),
         score, json.dumps(reasons, ensure_ascii=False), posted, offer_id),
    )


@retry_io
def refresh_all(path=None) -> None:
    with _write_lock, connect(path) as c:
        for (offer_id,) in c.execute("SELECT id FROM offers").fetchall():
            _refresh(c, offer_id)


@retry_io
def close_missing(run_id: int, cities: list[str], ok_sources: list[str], path=None) -> int:
    """Offres non revues alors que leur zone et leur(s) source(s) ont été interrogées."""
    if not cities or not ok_sources:
        return 0
    closed = 0
    ph_c, ph_s = ",".join("?" * len(cities)), ",".join("?" * len(ok_sources))
    with _write_lock, connect(path) as c:
        rows = c.execute(
            f"SELECT o.id, o.missed_runs FROM offers o WHERE o.is_active = 1 AND o.last_run_id != ? "
            f"AND o.hub IN ({ph_c}) AND EXISTS (SELECT 1 FROM listings l WHERE l.offer_id = o.id AND l.source IN ({ph_s}))",
            (run_id, *cities, *ok_sources),
        ).fetchall()
        for r in rows:
            missed = r["missed_runs"] + 1
            if missed >= MISSES_BEFORE_CLOSED:
                c.execute("UPDATE offers SET missed_runs=?, is_active=0, closed_at=? WHERE id=?", (missed, now_iso(), r["id"]))
                closed += 1
            else:
                c.execute("UPDATE offers SET missed_runs=? WHERE id=?", (missed, r["id"]))
    return closed


# ---------------------------------------------------------------------------
# Enrichissement incrémental
# ---------------------------------------------------------------------------

@retry_io
def offers_to_enrich(domains: tuple[str, ...], limit: int, path=None) -> list[tuple[int, int, str]]:
    """(offer_id, listing_id, url) des offres actives à la description trop courte, jamais lues."""
    if limit <= 0:
        return []
    like = " OR ".join("l.url LIKE ?" for _ in domains)
    with connect(path) as c:
        rows = c.execute(
            f"SELECT o.id AS oid, l.id AS lid, l.url FROM offers o JOIN listings l ON l.offer_id = o.id "
            f"WHERE o.is_active = 1 AND o.enriched = 0 AND o.enrich_attempts < 2 AND length(o.description) < 1000 "
            f"AND ({like}) ORDER BY o.first_seen DESC, o.id DESC",
            tuple(f"%{d}%" for d in domains),
        ).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["oid"] not in seen:
            seen.add(r["oid"])
            out.append((r["oid"], r["lid"], r["url"]))
        if len(out) >= limit:
            break
    return out


@retry_io
def save_enrichment(offer_id: int, listing_id: int, description: str, path=None) -> None:
    with _write_lock, connect(path) as c:
        if description:
            c.execute("UPDATE listings SET description=? WHERE id=?", (description, listing_id))
            c.execute("UPDATE offers SET enriched=1 WHERE id=?", (offer_id,))
        else:
            c.execute("UPDATE offers SET enrich_attempts = enrich_attempts + 1 WHERE id=?", (offer_id,))
        _refresh(c, offer_id)


# ---------------------------------------------------------------------------
# Suivi (statuts, notes)
# ---------------------------------------------------------------------------

@retry_io
def update_offer(offer_id: int, status: str | None = None, note: str | None = None, path=None) -> dict | None:
    with _write_lock, connect(path) as c:
        o = c.execute("SELECT status FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if not o:
            return None
        if status is not None and status in STATUSES and status != o["status"]:
            c.execute("UPDATE offers SET status=?, status_at=?, updated_at=? WHERE id=?", (status, now_iso(), now_iso(), offer_id))
            c.execute("INSERT INTO status_history (offer_id, status, at) VALUES (?,?,?)", (offer_id, status, now_iso()))
        if note is not None:
            c.execute("UPDATE offers SET note=?, updated_at=? WHERE id=?", (str(note)[:4000], now_iso(), offer_id))
    return get_offer(offer_id, path)


@retry_io
def split_listing(listing_id: int, path=None) -> int | None:
    """Sort une annonce de son offre (mauvais rapprochement) et empêche un nouveau rattachement."""
    with _write_lock, connect(path) as c:
        lst = c.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if not lst:
            return None
        count = c.execute("SELECT COUNT(*) FROM listings WHERE offer_id = ?", (lst["offer_id"],)).fetchone()[0]
        if count < 2:
            return lst["offer_id"]
        old = c.execute("SELECT * FROM offers WHERE id = ?", (lst["offer_id"],)).fetchone()
        today = date.today().isoformat()
        cur = c.execute(
            "INSERT INTO offers (title, company, location, hub, city_norm, first_seen, last_seen, first_run_id, "
            "last_run_id, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (lst["title"], lst["company"], lst["location"], lst["hub"], lst["city_norm"], old["first_seen"] or today,
             old["last_seen"] or today, old["first_run_id"], old["last_run_id"], old["is_active"], now_iso(), now_iso()),
        )
        new_id = int(cur.lastrowid)
        c.execute("UPDATE listings SET offer_id=?, match_confidence=NULL, match_reason='séparée manuellement' WHERE id=?",
                  (new_id, listing_id))
        c.execute("INSERT OR IGNORE INTO dedup_blocks (url, offer_id) VALUES (?, ?)", (lst["url"], lst["offer_id"]))
        _refresh(c, lst["offer_id"])
        _refresh(c, new_id)
        return new_id


@retry_io
def merge_offers(keep_id: int, other_id: int, path=None) -> bool:
    if keep_id == other_id:
        return False
    with _write_lock, connect(path) as c:
        keep = c.execute("SELECT * FROM offers WHERE id = ?", (keep_id,)).fetchone()
        other = c.execute("SELECT * FROM offers WHERE id = ?", (other_id,)).fetchone()
        if not keep or not other:
            return False
        status = keep["status"] or other["status"]
        note = "\n".join(n for n in (keep["note"], other["note"]) if n)
        c.execute("UPDATE listings SET offer_id=?, match_reason='fusionnée manuellement' WHERE offer_id=?", (keep_id, other_id))
        c.execute("UPDATE status_history SET offer_id=? WHERE offer_id=?", (keep_id, other_id))
        c.execute("DELETE FROM dedup_blocks WHERE offer_id IN (?, ?)", (keep_id, other_id))
        c.execute(
            "UPDATE offers SET status=?, note=?, first_seen=MIN(first_seen, ?), is_active=MAX(is_active, ?) WHERE id=?",
            (status, note, other["first_seen"], other["is_active"], keep_id),
        )
        c.execute("DELETE FROM offers WHERE id = ?", (other_id,))
        _refresh(c, keep_id)
    return True


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def _offer_dict(o: sqlite3.Row, listings: list[dict], history: list[dict]) -> dict:
    d = dict(o)
    d["score_reasons"] = json.loads(d.get("score_reasons") or "[]")
    d["signals"] = json.loads(d.pop("signals_json") or "{}")
    d["listings"] = listings
    d["sources"] = sorted({lst["source"] for lst in listings})
    d["url"] = max(listings, key=lambda lst: (len(lst["description"] or ""), lst["last_seen"] or ""))["url"] if listings else ""
    d["status_history"] = history
    for key in ("ff", "diploma_required", "beginner_ok", "is_active", "part_time", "enriched"):
        d[key] = bool(d.get(key))
    return d


@retry_io
def list_offers(include_inactive: bool = True, path=None) -> list[dict]:
    with connect(path) as c:
        offers = c.execute(
            "SELECT * FROM offers " + ("" if include_inactive else "WHERE is_active = 1 ") + "ORDER BY score DESC, id DESC"
        ).fetchall()
        listings = defaultdict(list)
        for row in c.execute(
            "SELECT id, offer_id, source, url, title, company, location, description, posted_date, first_seen, last_seen, "
            "match_confidence, match_reason FROM listings ORDER BY id"
        ):
            d = dict(row)
            d["description"] = (d["description"] or "")[:300]
            listings[d["offer_id"]].append(d)
        history = defaultdict(list)
        for h in c.execute("SELECT offer_id, status, at FROM status_history ORDER BY id"):
            history[h["offer_id"]].append({"status": h["status"], "at": h["at"]})
    result = [_offer_dict(o, listings[o["id"]], history[o["id"]]) for o in offers]
    for decorate in extensions.OFFER_DECORATORS:
        decorate(result)
    return result


@retry_io
def get_offer(offer_id: int, path=None) -> dict | None:
    with connect(path) as c:
        o = c.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if not o:
            return None
        listings = [dict(r) for r in c.execute("SELECT * FROM listings WHERE offer_id = ? ORDER BY id", (offer_id,))]
        history = [dict(h) for h in c.execute("SELECT status, at FROM status_history WHERE offer_id = ? ORDER BY id", (offer_id,))]
    result = [_offer_dict(o, listings, history)]
    for decorate in extensions.OFFER_DECORATORS:
        decorate(result)
    return result[0]


def companies(path=None) -> list[dict]:
    """Vue employeurs : volume, récurrence, candidatures déjà envoyées."""
    groups: dict[str, dict] = {}
    for o in list_offers(True, path):
        key = intel.norm_company(o["company"])
        if not key:
            continue
        g = groups.setdefault(key, {
            "name": o["company"], "offers": 0, "active": 0, "ff": 0, "reposted": 0, "applied": 0,
            "cities": set(), "sources": set(), "first_seen": o["first_seen"], "last_seen": o["last_seen"], "offer_ids": [],
        })
        g["offers"] += 1
        g["active"] += int(o["is_active"])
        g["ff"] += int(o["ff"])
        g["reposted"] += o["reposted"]
        g["applied"] += int(o["status"] in {"postule", "relance", "entretien", "offre", "refuse"})
        g["cities"].add(o["hub"])
        g["sources"].update(o["sources"])
        g["first_seen"] = min(g["first_seen"] or "", o["first_seen"] or "") or g["first_seen"]
        g["last_seen"] = max(g["last_seen"] or "", o["last_seen"] or "")
        g["offer_ids"].append(o["id"])
        if len(o["company"]) > len(g["name"]):
            g["name"] = o["company"]
    out = []
    for g in groups.values():
        g["cities"] = sorted(c for c in g["cities"] if c)
        g["sources"] = sorted(g["sources"])
        out.append(g)
    return sorted(out, key=lambda g: (-g["active"], -g["offers"]))


@retry_io
def data_version(path=None) -> str:
    with connect(path) as c:
        r = c.execute("SELECT COUNT(*), COALESCE(MAX(updated_at), ''), (SELECT COUNT(*) FROM listings), "
                      "(SELECT COALESCE(MAX(id), 0) FROM status_history) FROM offers").fetchone()
    return "|".join(str(x) for x in r)


# ---------------------------------------------------------------------------
# Exports (compatibles auto_apply.py)
# ---------------------------------------------------------------------------

EXPORT_FIELDS = [
    "title", "company", "location", "contract_type", "salary", "faisant_fonction", "diplome_exige", "score",
    "distance_km", "description", "posted_date", "apply_url", "source", "search_hub", "nb_sites", "statut", "note",
]


@retry_io
def export_files(csv_path: Path, json_path: Path, path=None) -> int:
    offers = [o for o in list_offers(False, path) if o["status"] != "masque"]
    rows = [{
        "title": o["title"], "company": o["company"], "location": o["location"], "contract_type": o["contract"] or o["contract_raw"],
        "salary": o["salary_text"], "faisant_fonction": o["ff"], "diplome_exige": o["diploma_required"], "score": o["score"],
        "distance_km": o["distance_km"], "description": o["description"][:500], "posted_date": o["posted_date"],
        "apply_url": o["url"], "source": ", ".join(o["sources"]), "search_hub": o["hub"], "nb_sites": len(o["sources"]),
        "statut": o["status"], "note": o["note"],
    } for o in offers]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_FIELDS)
        w.writeheader()
        w.writerows(rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": {"extracted_at": now_iso(), "total_offers": len(rows),
                                "faisant_fonction_matches": sum(r["faisant_fonction"] for r in rows)},
                   "offers": rows}, f, ensure_ascii=False, indent=2)
    return len(rows)


# ---------------------------------------------------------------------------
# Import des anciens fichiers (une seule fois)
# ---------------------------------------------------------------------------

def import_legacy(json_path: Path, marks_path: Path, history_path: Path, path=None) -> str:
    with connect(path) as c:
        if c.execute("SELECT value FROM meta WHERE key = 'legacy_imported'").fetchone():
            return ""
    parts = []
    try:
        offers = json.loads(json_path.read_text(encoding="utf-8")).get("offers", [])
    except (OSError, ValueError):
        offers = []
    offers = [o for o in offers if o.get("apply_url") and "," not in str(o.get("source", ""))]
    if offers:
        run_id = start_run({"import": str(json_path.name)}, trigger="import", path=path)
        st = ingest(offers, run_id, path=path)
        finish_run(run_id, path=path, status="ok", listings=st["listings"], new_offers=st["new_offers"], merged=st["merged"])
        parts.append(f"{st['listings']} annonces importées → {st['new_offers']} offres ({st['merged']} doublons fusionnés)")
    try:
        marks = json.loads(marks_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        marks = {}
    applied = 0
    if marks:
        with connect(path) as c:
            url_to_offer = {r["url"]: r["offer_id"] for r in c.execute("SELECT url, offer_id FROM listings")}
        for url, m in marks.items():
            oid = url_to_offer.get(url)
            if oid and (m.get("status") or m.get("note")):
                update_offer(oid, m.get("status"), m.get("note"), path=path)
                applied += 1
        parts.append(f"{applied} suivis importés")
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        history = []
    with _write_lock, connect(path) as c:
        for h in history:
            c.execute(
                "INSERT INTO runs (started_at, finished_at, status, trigger, config_json, duration_s, offers_active, ff, "
                "new_offers, per_source_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (h.get("started_at"), h.get("finished_at"), h.get("status", "ok"), "ancien", json.dumps(h.get("config") or {}),
                 h.get("duration_s", 0), h.get("total", 0), h.get("ff", 0), h.get("new", 0), json.dumps(h.get("per_source") or {})),
            )
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_imported', ?)", (now_iso(),))
    if history:
        parts.append(f"{len(history)} recherches d'historique importées")
    return " · ".join(parts)
