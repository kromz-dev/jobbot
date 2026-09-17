"""
JobBot - Sources HTML (sans clé API, sans IA)

  - Hellowork       (hellowork.com)            cartes SSR + JSON-LD détail
  - France Travail  (candidat.francetravail.fr) HTML + pagination Tapestry
  - Meteojob        (meteojob.com)             cartes SSR + JSON-LD détail
  - Jobijoba        (jobijoba.com)             agrégateur (Appel Médical, Adecco…)

Chaque scraper renvoie un DataFrame au format colonnes JobSpy
(title, company, location, job_type, description, date_posted, job_url, site…)
pour réutiliser row_to_job() de scraper.py tel quel.
"""

from __future__ import annotations

import html
import json
import random
import re
import threading
import time
from collections.abc import Callable
from datetime import date, timedelta
from urllib.parse import quote_plus, urljoin

import pandas as pd
from bs4 import BeautifulSoup
from curl_cffi import requests as cr

LogFn = Callable[[str], None]

MAX_PAGES = 3
HTTP_RETRIES = 3
TIMEOUT = 30

# Codes INSEE (vérifiés via geo.api.gouv.fr)
INSEE = {
    "Pamiers": "09225",
    "Mirepoix": "09194",
    "Muret": "31395",
    "Toulouse": "31555",
}

COLUMNS = [
    "title", "company", "location", "job_type", "description", "date_posted",
    "job_url", "site", "min_amount", "max_amount", "currency", "interval",
]

# Une session curl_cffi par thread (pas thread-safe) + max 2 requêtes / domaine
_local = threading.local()
_domain_sem: dict[str, threading.Semaphore] = {}
_sem_lock = threading.Lock()


class SourceError(Exception):
    """Échec définitif d'une source (après retries)."""


def _session() -> cr.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = cr.Session(impersonate="chrome")
        _local.session = s
    return s


def _sem(url: str) -> threading.Semaphore:
    domain = url.split("/")[2]
    with _sem_lock:
        return _domain_sem.setdefault(domain, threading.Semaphore(2))


def http_get(url: str, headers: dict | None = None) -> cr.Response:
    """GET avec empreinte Chrome, retry + backoff, politesse par domaine."""
    last: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            with _sem(url):
                time.sleep(random.uniform(0.3, 0.9))
                r = _session().get(url, headers=headers, timeout=TIMEOUT)
            # France Travail renvoie 410 sur les pages SEO mais le contenu est valide
            if r.status_code in (429, 500, 502, 503, 504):
                raise SourceError(f"HTTP {r.status_code}")
            if r.status_code >= 400 and r.status_code != 410:
                raise SourceError(f"HTTP {r.status_code} sur {url[:80]}")
            return r
        except Exception as e:
            last = e
            if isinstance(e, SourceError) and "HTTP 4" in str(e) and "429" not in str(e):
                break
            time.sleep(1.5 * 2 ** (attempt - 1))
    raise SourceError(str(last)[:120]) from last


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def _row(**kw) -> dict:
    row = dict.fromkeys(COLUMNS, None)
    row.update(currency="EUR", interval="")
    row.update(kw)
    return row


def parse_relative_date(text: str) -> str:
    """'il y a 3 jours' / 'Publié hier' / 'Il y a 10 h' → YYYY-MM-DD."""
    t = (text or "").lower()
    today = date.today()
    if not t:
        return ""
    if "aujourd" in t or re.search(r"\d+\s*(h|min|heure)", t):
        return today.isoformat()
    if "hier" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*(jour|semaine|mois)", t)
    if m:
        n = int(m.group(1))
        days = {"jour": 1, "semaine": 7, "mois": 30}[m.group(2)] * n
        return (today - timedelta(days=days)).isoformat()
    if "plus de 30" in t:
        return (today - timedelta(days=31)).isoformat()
    return ""


def _city(city: str) -> str:
    return city.split(",")[0].strip()


def resolve_insee(city: str) -> str:
    """Code INSEE d'une commune (cache + geo.api.gouv.fr pour les villes ajoutées)."""
    if city in INSEE:
        return INSEE[city]
    try:
        r = _session().get(
            f"https://geo.api.gouv.fr/communes?nom={quote_plus(city)}&fields=code,population&boost=population&limit=1",
            timeout=TIMEOUT,
        )
        found = r.json()
    except Exception:
        return ""
    code = found[0]["code"] if found else ""
    if code:
        INSEE[city] = code
    return code


# ---------------------------------------------------------------------------
# Hellowork
# ---------------------------------------------------------------------------

def scrape_hellowork(
    term: str, city: str, log: LogFn | None = None, radius_km: int = 10, max_pages: int = MAX_PAGES,
) -> pd.DataFrame:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        url = (
            "https://www.hellowork.com/fr-fr/emploi/recherche.html"
            f"?k={quote_plus(term)}&l={quote_plus(_city(city))}&ray={radius_km}&p={page}"
        )
        soup = BeautifulSoup(http_get(url).text, "lxml")
        cards = soup.select('[data-cy="serpCard"]')
        for c in cards:
            a = c.select_one('a[data-cy="offerTitle"]')
            if not a:
                continue
            ps = a.find_all("p")
            tags = [_txt(t) for t in c.select("div.tag-secondary-s")]
            salary = next((t for t in tags if "€" in t), "")
            rows.append(_row(
                title=_txt(ps[0]) if ps else a.get("title", ""),
                company=_txt(ps[1]) if len(ps) > 1 else "",
                location=_txt(c.select_one('[data-cy="localisationCard"]')),
                job_type=_txt(c.select_one('[data-cy="contractCard"]')),
                description=" · ".join(t for t in tags if not t.startswith("+")),
                date_posted=parse_relative_date(_txt(c)[-40:]),
                job_url=urljoin("https://www.hellowork.com", a["href"]),
                site="Hellowork",
                salary_text=salary,
            ))
        if len(cards) < 20:
            break
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# France Travail (site candidat, pas l'API OAuth)
# ---------------------------------------------------------------------------

FT_BASE = "https://candidat.francetravail.fr"


def _ft_parse(fragment: str) -> list[dict]:
    soup = BeautifulSoup(fragment, "lxml")
    rows = []
    for li in soup.select("li.result"):
        oid = li.get("data-id-offre")
        if not oid:
            continue
        sub_el = li.select_one("p.subtext")
        loc_el = sub_el.find("span") if sub_el else None
        loc = _txt(loc_el)
        if loc_el:
            loc_el.extract()
        company = _txt(sub_el).rstrip(" -")
        contrat = li.select_one("p.contrat.visible-xs")
        rows.append(_row(
            title=_txt(li.select_one(".media-heading-title")),
            company=company,
            location=loc,
            job_type=_txt(contrat).replace("\xa0", " "),
            description=_txt(li.select_one("p.description")),
            date_posted=parse_relative_date(_txt(li.select_one("p.date"))),
            job_url=f"{FT_BASE}/offres/recherche/detail/{oid}",
            site="France Travail",
        ))
    return rows


def scrape_francetravail(
    term: str, city: str, log: LogFn | None = None, radius_km: int = 10, max_pages: int = MAX_PAGES,
) -> pd.DataFrame:
    code = resolve_insee(_city(city))
    if not code:
        if log:
            log(f"  France Travail: commune inconnue '{_city(city)}'")
        return pd.DataFrame()
    # tri=1 → plus récentes d'abord
    url = f"{FT_BASE}/offres/recherche?motsCles={quote_plus(term)}&lieux={code}&rayon={radius_km}&tri=1"
    first = http_get(url)
    rows = _ft_parse(first.text)
    m = re.search(r'/offres/emploi\.rechercheoffre:afficherplusderesultats/20-39/0\?t:ac=[^"\']+', first.text)
    if m:
        for page in range(1, max_pages):
            rng = f"{20 * page}-{20 * page + 19}"
            more = FT_BASE + html.unescape(m.group(0)).replace("20-39", rng)
            r = http_get(more, headers={"X-Requested-With": "XMLHttpRequest", "Referer": first.url})
            try:
                chunks = r.json()["_tapestry"]["content"]
            except (ValueError, KeyError, TypeError):
                break
            new = [row for _, frag in chunks for row in _ft_parse(frag)]
            if not new:
                break
            rows.extend(new)
            if len(new) < 20:
                break
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Meteojob
# ---------------------------------------------------------------------------

def scrape_meteojob(
    term: str, city: str, log: LogFn | None = None, radius_km: int = 10, max_pages: int = MAX_PAGES,
) -> pd.DataFrame:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        url = (
            "https://www.meteojob.com/jobs"
            f"?what={quote_plus(term)}&where={quote_plus(_city(city))}&page={page}"
        )
        soup = BeautifulSoup(http_get(url).text, "lxml")
        cards = soup.select("article.cc-job-offer")
        for c in cards:
            a = c.select_one("a.cc-job-offer-list-item__link")
            if not a:
                continue
            salary = _txt(c.select_one('[id$="-salary"]'))
            date_txt = next((s for s in c.stripped_strings if s.lower().startswith("il y a")), "")
            rows.append(_row(
                title=_txt(a),
                company=_txt(c.select_one('[id$="-company-name"]')),
                location=_txt(c.select_one('[id$="-job-locations"]')).replace("place", "", 1).strip(),
                job_type=_txt(c.select_one('[id$="-contract-types"]')),
                description="",
                date_posted=parse_relative_date(date_txt),
                job_url=urljoin("https://www.meteojob.com", a["href"]),
                site="Meteojob",
                salary_text="" if "non précisé" in salary else salary,
            ))
        if len(cards) < 15:
            break
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Jobijoba (agrégateur)
# ---------------------------------------------------------------------------

def scrape_jobijoba(
    term: str, city: str, log: LogFn | None = None, radius_km: int = 10, max_pages: int = MAX_PAGES,
) -> pd.DataFrame:
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        url = (
            "https://www.jobijoba.com/fr/query/"
            f"?what={quote_plus(term)}&where={quote_plus(_city(city))}"
            f"&where_type=city&perimeter={radius_km}&page={page}"
        )
        soup = BeautifulSoup(http_get(url).text, "lxml")
        cards = soup.select("div.offer")
        for c in cards:
            a = c.select_one("a.offer-link")
            if not a:
                continue
            def feat(icon: str, card=c) -> str:
                ic = card.select_one(f".offer-features .{icon}")
                return _txt(ic.find_parent(class_="feature")) if ic else ""

            where = feat("icon-map-marker")
            rows.append(_row(
                title=_txt(c.select_one(".offer-header-title")),
                company=feat("icon-apartment"),
                location=re.sub(r"\s*\(à .*?\)", "", where),
                job_type="",
                description=_txt(c.select_one(".description")),
                date_posted=parse_relative_date(_txt(c.select_one(".publication_date"))),
                job_url=a["href"],
                site="Jobijoba",
            ))
        if len(cards) < 30:
            break
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Enrichissement : description complète (pour détecter "faisant fonction")
# ---------------------------------------------------------------------------

def _jsonld_jobposting(soup: BeautifulSoup) -> dict | None:
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.string or "")
        except (ValueError, TypeError):
            continue
        items = data.get("@graph", [data]) if isinstance(data, dict) else data
        for it in items if isinstance(items, list) else [items]:
            if isinstance(it, dict) and it.get("@type") == "JobPosting":
                return it
    return None


def fetch_description(url: str) -> str:
    """Récupère la description complète d'une offre (Hellowork, Meteojob, France Travail)."""
    soup = BeautifulSoup(http_get(url).text, "lxml")
    if "francetravail.fr" in url:
        return _txt(soup.select_one('[itemprop="description"]'))
    jp = _jsonld_jobposting(soup)
    if jp and jp.get("description"):
        return BeautifulSoup(html.unescape(jp["description"]), "lxml").get_text(" ", strip=True)
    return ""


ENRICHABLE = ("hellowork.com", "meteojob.com", "francetravail.fr")


SOURCES: dict[str, Callable[..., pd.DataFrame]] = {
    "Hellowork": scrape_hellowork,
    "France Travail": scrape_francetravail,
    "Meteojob": scrape_meteojob,
    "Jobijoba": scrape_jobijoba,
}
