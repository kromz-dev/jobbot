"""
Collecte multi-sources : Indeed (JobSpy) + Hellowork, France Travail, Meteojob, Jobijoba.

Rôle : interroger chaque site pour chaque ville et mot-clé, garder les annonces pertinentes,
calculer leur distance réelle et écarter le hors-périmètre. Le rapprochement des doublons
entre sites et le score sont faits ensuite en base (db.ingest, intel).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore

import pandas as pd
from jobspy import scrape_jobs

from . import geo, intel
from .sources import SOURCES, SourceError, resolve_insee

MAX_WORKERS = 6            # tâches parallèles (limite de 2 requêtes simultanées par site dans sources.py)
INDEED_CONCURRENCY = 2
MAX_RETRIES = 3
RETRY_BASE_SEC = 1.5

CITY_SHORT = ["Pamiers", "Mirepoix", "Muret", "Toulouse"]
CITY_QUERIES = {c: f"{c}, Occitanie" for c in CITY_SHORT}

# Indeed accepte guillemets et OR
SEARCH_TERMS = [
    "aide-soignant",
    '"aide soignant" "faisant fonction"',
    '"AS FF" aide-soignant OR "aide soignante"',
]
# Sites sans syntaxe booléenne
SOURCE_TERMS = [
    "aide soignant",
    "aide soignant faisant fonction",
]

def all_sources() -> list[str]:
    """Sources disponibles, y compris celles ajoutées par une fonctionnalité."""
    return ["Indeed", *SOURCES.keys()]


ALL_SOURCES = all_sources()  # compatibilité : valeur au démarrage

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int, str], None]
StatFn = Callable[[dict], None]

DEFAULT_CONFIG: dict = {
    "sources": ALL_SOURCES,
    "cities": CITY_SHORT,
    "indeed_terms": SEARCH_TERMS,
    "source_terms": SOURCE_TERMS,
    "radius_km": 10,
    "city_radius": {},        # rayon propre à une ville, ex. {"Mirepoix": 15}
    "strict_radius": True,    # écarte les offres situées au-delà du rayon (distance réelle)
    "max_pages": 3,
    "enrich": True,
    "max_enrich": 200,
    "integrations": {},       # identifiants d'API optionnels, ex. {"francetravail": {"client_id": "…"}}
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def normalize_config(raw: dict | None) -> dict:
    """Fusionne une config (tableau de bord / config.json) avec les valeurs par défaut, en la bornant."""
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_CONFIG.items()}
    raw = raw or {}

    for key in ("sources", "cities", "indeed_terms", "source_terms"):
        val = raw.get(key)
        if isinstance(val, list):
            cfg[key] = list(dict.fromkeys(str(x).strip() for x in val if str(x).strip()))
    available = all_sources()
    if "sources" not in raw:
        cfg["sources"] = available
    cfg["sources"] = [src for src in cfg["sources"] if src in available]

    for key, lo, hi in (("radius_km", 1, 100), ("max_pages", 1, 10), ("max_enrich", 0, 1000)):
        try:
            cfg[key] = max(lo, min(hi, int(raw.get(key, cfg[key]))))
        except (TypeError, ValueError):
            pass
    for key in ("enrich", "strict_radius"):
        if key in raw:
            cfg[key] = bool(raw[key])

    city_radius = {}
    if isinstance(raw.get("city_radius"), dict):
        for city, val in raw["city_radius"].items():
            try:
                city_radius[str(city)] = max(1, min(100, int(val)))
            except (TypeError, ValueError):
                continue
    cfg["city_radius"] = {c: r for c, r in city_radius.items() if c in cfg["cities"] and r != cfg["radius_km"]}

    integrations = raw.get("integrations")
    cfg["integrations"] = {
        str(name): {str(k): str(v)[:500] for k, v in values.items()}
        for name, values in (integrations.items() if isinstance(integrations, dict) else [])
        if isinstance(values, dict)
    }
    return cfg


def radius_for(cfg: dict, city: str) -> int:
    return int(cfg["city_radius"].get(city, cfg["radius_km"]))


# ---------------------------------------------------------------------------
# Normalisation d'une ligne (format colonnes JobSpy)
# ---------------------------------------------------------------------------

def _clean(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def _salary(row) -> str:
    mn, mx = row.get("min_amount"), row.get("max_amount")
    if pd.notna(mn) or pd.notna(mx):
        parts = [str(int(v)) if float(v) == int(v) else str(v) for v in (mn, mx) if pd.notna(v)]
        text = " - ".join(parts) + f" {_clean(row.get('currency')) or 'EUR'}"
        interval = _clean(row.get("interval"))
        return f"{text} / {interval}" if interval else text
    return _clean(row.get("salary_text"))


def row_to_listing(row, search_city: str) -> dict | None:
    title, desc = _clean(row.get("title")), _clean(row.get("description"))
    if not intel.is_relevant(title, desc):
        return None
    return {
        "title": title,
        "company": _clean(row.get("company")),
        "location": _clean(row.get("location")) or search_city,
        "contract_type": _clean(row.get("job_type")),
        "salary": _salary(row),
        "description": desc[:8000],
        "posted_date": _clean(row.get("date_posted")),
        "apply_url": _clean(row.get("job_url")),
        "source": _clean(row.get("site")) or "JobSpy",
        "search_hub": search_city,
    }


# ---------------------------------------------------------------------------
# Indeed (JobSpy) avec retry
# ---------------------------------------------------------------------------

_TRANSIENT = ("429", "timeout", "timed out", "connection", "reset", "503", "502")


def scrape_indeed(term: str, city: str, distance_miles: int, log: LogFn | None = None) -> pd.DataFrame:
    """Lève SourceError en cas d'échec définitif."""
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = scrape_jobs(site_name=["indeed"], search_term=term, location=city, distance=distance_miles,
                             results_wanted=30, country_indeed="France", verbose=0)
            return df if df is not None else pd.DataFrame()
        except Exception as e:  # noqa: BLE001 — JobSpy lève des exceptions génériques
            if not any(x in str(e).lower() for x in _TRANSIENT):
                raise SourceError(f"Indeed: {str(e)[:100]}") from e
            last = e
            wait = RETRY_BASE_SEC * 2 ** (attempt - 1)
            if log:
                log(f"  Indeed: nouvel essai {attempt}/{MAX_RETRIES} dans {wait:.1f}s ({str(e)[:60]})")
            time.sleep(wait)
    raise SourceError(f"Indeed: echec apres {MAX_RETRIES} essais ({last})") from last


# ---------------------------------------------------------------------------
# Périmètre (distance réelle)
# ---------------------------------------------------------------------------

def apply_perimeter(listings: list[dict], cfg: dict, log: LogFn) -> list[dict]:
    """Distance réelle annonce → ville la plus proche ; écarte le hors-périmètre si strict."""
    centers = {}
    for city in cfg["cities"]:
        code = resolve_insee(city)
        point = (code and geo.geocode_insee(code)) or geo.geocode(city)
        if point:
            centers[city] = point
        else:
            log(f"  Perimetre: ville introuvable '{city}' (pas de filtre distance pour elle)")
    if not centers:
        return listings

    places = {geo.parse_location(item.get("location", "")) for item in listings}
    with ThreadPoolExecutor(max_workers=6) as pool:
        points = dict(zip(places, pool.map(lambda p: geo.geocode(*p), places), strict=True))
    geo.save_cache()

    kept, dropped, unknown = [], 0, 0
    for item in listings:
        point = points.get(geo.parse_location(item.get("location", "")))
        if not point:
            unknown += 1
            item["distance_km"] = None
            kept.append(item)
            continue
        city, dist = min(((c, geo.haversine_km(point, p)) for c, p in centers.items()), key=lambda x: x[1])
        item["distance_km"] = round(dist, 1)
        item["search_hub"] = city
        if cfg["strict_radius"] and dist > radius_for(cfg, city):
            dropped += 1
            continue
        kept.append(item)
    log(f"Perimetre: {len(kept)} annonces gardees, {dropped} hors rayon ecartees, {unknown} lieux non localises (gardees)")
    return kept


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def scrape_all(
    on_log: LogFn | None = None,
    on_progress: ProgressFn | None = None,
    stop_flag: Callable[[], bool] | None = None,
    config: dict | None = None,
    on_stat: StatFn | None = None,
    max_workers: int = MAX_WORKERS,
) -> list[dict]:
    """Collecte parallèle pilotée par `config`. Renvoie les annonces (dédoublonnées par URL) dans le périmètre."""
    cfg = normalize_config(config)
    log = on_log or print
    stopped = lambda: bool(stop_flag and stop_flag())  # noqa: E731

    queries = {c: CITY_QUERIES.get(c) or (c if "," in c else f"{c}, France") for c in cfg["cities"]}
    tasks = [
        (source, city, term)
        for source in cfg["sources"]
        for city in cfg["cities"]
        for term in (cfg["indeed_terms"] if source == "Indeed" else cfg["source_terms"])
    ]
    total = len(tasks)
    seen: set[str] = set()
    listings: list[dict] = []
    lock = Lock()
    indeed_sem = Semaphore(INDEED_CONCURRENCY)
    stats = {src: {"passes": 0, "done": 0, "raw": 0, "added": 0, "errors": 0, "seconds": 0.0, "last_error": ""}
             for src in cfg["sources"]}
    for source, _, _ in tasks:
        stats[source]["passes"] += 1

    def push_stats() -> None:
        if on_stat:
            on_stat({k: dict(v) for k, v in stats.items()})

    push_stats()
    radii = ", ".join(f"{c} {radius_for(cfg, c)} km" for c in cfg["cities"])
    log(f"Demarrage — sources: {', '.join(cfg['sources']) or 'aucune'}")
    log(f"{len(cfg['cities'])} villes ({radii}) | {total} passes | {cfg['max_pages']} page(s) | workers={max_workers}")

    def one_task(source: str, city: str, term: str) -> tuple[list[dict], int, str, float]:
        radius, query = radius_for(cfg, city), queries[city]
        t0, error, df = time.monotonic(), "", pd.DataFrame()
        try:
            if source == "Indeed":
                with indeed_sem:
                    df = scrape_indeed(term, query, math.ceil(radius / 1.609), log=log)
            else:
                df = SOURCES[source](term, query, log=log, radius_km=radius, max_pages=cfg["max_pages"])
        except SourceError as e:
            error = str(e)
            log(f"  {source} @ {city}: {e}")
        rows = [] if df is None or df.empty else [r for _, r in df.iterrows()]
        found = [item for item in (row_to_listing(r, city) for r in rows) if item]
        return found, len(rows), error, time.monotonic() - t0

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for task in tasks:
            if stopped():
                break
            futures[pool.submit(one_task, *task)] = task

        for fut in as_completed(futures):
            if stopped():
                for f in futures:
                    f.cancel()
                log("Arret demande.")
                break
            source, city, term = futures[fut]
            label = f'{source}: "{term}" @ {city}'
            done += 1
            if on_progress:
                on_progress(done, total, label)
            try:
                found, raw_n, error, secs = fut.result()
            except Exception as e:  # noqa: BLE001 — une source ne doit pas arrêter les autres
                found, raw_n, error, secs = [], 0, str(e)[:100], 0.0
                log(f"[{done}/{total}] {label} — erreur: {error}")

            with lock:
                added = 0
                for item in found:
                    if item["apply_url"] and item["apply_url"] not in seen:
                        seen.add(item["apply_url"])
                        listings.append(item)
                        added += 1
                st = stats[source]
                st["done"] += 1
                st["raw"] += raw_n
                st["added"] += added
                st["seconds"] = round(st["seconds"] + secs, 1)
                if error:
                    st["errors"] += 1
                    st["last_error"] = error
            push_stats()
            log(f"[{done}/{total}] {label} — {raw_n} bruts → +{added} annonces (total {len(listings)})")

    if listings and not stopped():
        listings = apply_perimeter(listings, cfg, log)
    log(f"Collecte terminee: {len(listings)} annonces dans le perimetre")
    return listings
