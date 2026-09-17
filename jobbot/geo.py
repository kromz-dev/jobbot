"""
JobBot - Géolocalisation des offres (geo.api.gouv.fr, gratuit, sans clé)

Sert à garantir un vrai périmètre : chaque offre reçoit sa distance (km) à la
ville de recherche la plus proche, et peut être écartée si elle dépasse le rayon.
Les sites d'emploi appliquent le rayon de façon approximative (Meteojob l'ignore).
"""

from __future__ import annotations

import json
import math
import re
import threading
import unicodedata
from urllib.parse import quote_plus

from curl_cffi import requests as cr

from .paths import GEO_CACHE_PATH as CACHE_PATH

API = "https://geo.api.gouv.fr/communes"
TIMEOUT = 15

_lock = threading.Lock()
_cache: dict[str, list[float] | None] | None = None


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def save_cache() -> None:
    with _lock:
        if _cache is not None:
            CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def parse_location(location: str) -> tuple[str, str]:
    """'Pamiers - 09' / '09 - BENAGUES' / 'Foix (09)' / 'Toulouse, O, FR' → (commune, département)."""
    loc = (location or "").strip()
    dept = ""
    m = re.match(r"^(\d{2}|2[AB]|97\d)\s*-\s*(.+)$", loc)                 # France Travail
    if m:
        return m.group(2).strip(), m.group(1)
    m = re.match(r"^(.+?)\s*[-(]\s*(\d{2}|2[AB]|97\d)\s*\)?\s*$", loc)     # Hellowork / Meteojob
    if m:
        return m.group(1).strip(), m.group(2)
    name = loc.split(",")[0].strip()                                      # Indeed / Jobijoba
    name = re.sub(r"\s*\d{5}\s*", " ", name).strip()
    return name, dept


def _variants(name: str) -> list[str]:
    n = re.sub(r"\s+", " ", name).strip()
    out = [n]
    fixed = re.sub(r"^(st|ste)\s+", lambda m: "Saint-" if m.group(1).lower() == "st" else "Sainte-", n, flags=re.I)
    fixed = re.sub(r"^(l|d)\s+(?=[aeiouyh])", r"\1'", fixed, flags=re.I)
    if fixed != n:
        out.append(fixed)
    out.append(fixed.replace(" ", "-"))
    return list(dict.fromkeys(out))


def _key(name: str, dept: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c)).lower()
    return f"{folded}|{dept}"


def geocode(name: str, dept: str = "") -> tuple[float, float] | None:
    """Centre de la commune (lon, lat), avec cache disque."""
    if not name:
        return None
    cache = _load()
    key = _key(name, dept)
    if key in cache:
        return tuple(cache[key]) if cache[key] else None
    point = None
    for variant in _variants(name):
        url = f"{API}?nom={quote_plus(variant)}&fields=centre,codeDepartement&boost=population&limit=1"
        if dept:
            url += f"&codeDepartement={dept}"
        try:
            found = cr.get(url, impersonate="chrome", timeout=TIMEOUT).json()
        except Exception:
            return None  # erreur réseau : ne pas mettre en cache
        if found and found[0].get("centre"):
            point = found[0]["centre"]["coordinates"]
            break
    with _lock:
        cache[key] = point
    return tuple(point) if point else None


def geocode_insee(code: str) -> tuple[float, float] | None:
    cache = _load()
    key = f"insee|{code}"
    if key in cache:
        return tuple(cache[key]) if cache[key] else None
    try:
        data = cr.get(f"{API}/{code}?fields=centre", impersonate="chrome", timeout=TIMEOUT).json()
        point = data["centre"]["coordinates"]
    except Exception:
        return None
    with _lock:
        cache[key] = point
    return tuple(point)
