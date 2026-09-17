"""
JobBot - Intelligence métier (sans IA générative, 100 % local et explicable)

  - normalisation (titres, employeurs, villes, contrats, horaires)
  - extraction de signaux : faisant fonction, diplôme exigé, débutant accepté, nuit…
  - salaire → fourchette mensuelle brute
  - score de pertinence avec raisons lisibles
  - comparaison de deux annonces : même offre ou non (avec confiance + explication)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def fold(text: str | None) -> str:
    """Minuscules, sans accents, espaces compactés."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    out = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", out).strip()


_GENDER = re.compile(r"\(?\b[hf]\s*/\s*[hf](\s*/\s*x)?\b\)?|\(\s*e\s*\)|\.e\b|\(e\)")
_TITLE_NOISE_WORDS = {
    "cdi", "cdd", "interim", "h", "f", "x", "hf", "fh", "poste", "offre", "emploi", "recrute", "urgent",
    "temps", "plein", "partiel", "de", "d", "en", "a", "au", "aux", "le", "la", "les", "l", "un", "une", "et", "ou",
    "pour", "sur", "du", "des",
}
_TITLE_SYNONYMS = [
    (re.compile(r"\baides?[- ]soignant(e|es|s)?\b"), "aidesoignant"),
    (re.compile(r"\b(as|a\.s\.)\b"), "aidesoignant"),
    (re.compile(r"\bfaisant[- ]fonctions?\b|\bff\b"), "faisantfonction"),
    (re.compile(r"\bdiplome(e|s|es)?\b|\bdeas\b"), "diplome"),
]


def norm_title(title: str | None) -> str:
    t = _GENDER.sub(" ", fold(title))
    for rx, rep in _TITLE_SYNONYMS:
        t = rx.sub(rep, t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    words = [w for w in t.split() if w not in _TITLE_NOISE_WORDS and not w.isdigit()]
    return " ".join(words)


_LEGAL = re.compile(
    r"\b(sas|sasu|sarl|sa|eurl|sci|selarl|scop|association|asso|groupe|group|france|sante|care|medical)\b"
)
GENERIC_COMPANIES = {"", "interim", "confidentiel", "entreprise", "employeur", "non communique", "societe"}


def norm_company(company: str | None) -> str:
    c = fold(company)
    c = re.sub(r"[^a-z0-9 ]", " ", c)
    c = _LEGAL.sub(" ", c)
    c = re.sub(r"\s+", " ", c).strip()
    return "" if c in GENERIC_COMPANIES else c


def norm_city(location: str | None) -> str:
    loc = fold(location)
    loc = re.sub(r"^\d{2,3}\s*-\s*", "", loc)
    loc = re.split(r"\s+-\s+|\s*\(|,", loc)[0]
    loc = re.sub(r"^st(e)?\b", lambda m: "saint" + ("e" if m.group(1) else ""), loc)
    return re.sub(r"[^a-z]", "", loc)


def contract_kind(raw: str | None) -> str:
    c = fold(raw)
    if re.search(r"interim|temporary|mission", c):
        return "Intérim"
    if re.search(r"\bcdd\b|contract|remplacement", c):
        return "CDD"
    if re.search(r"\bcdi\b|permanent", c):
        return "CDI"
    if re.search(r"stage|intern|alternance|apprenti", c):
        return "Stage / alternance"
    if re.search(r"vacation|liberal|independant|freelance", c):
        return "Vacation / libéral"
    return ""


def shift_kind(title: str | None, description: str | None = "") -> str:
    t = fold(title)
    night, day = bool(re.search(r"\bnuit\b", t)), bool(re.search(r"\bjour\b", t))
    if night != day:
        return "nuit" if night else "jour"
    d = fold(description)[:1500]
    if re.search(r"\b(poste|travail|horaires?) de nuit\b|\bde nuit\b", d) and not re.search(r"\bjour\b", d[:400]):
        return "nuit"
    return ""


# ---------------------------------------------------------------------------
# Signaux métier
# ---------------------------------------------------------------------------

FF_PATTERNS = [
    r"faisant[- ]fonctions?", r"\bas\s*ff\b", r"\basff\b", r"\bff\s*as\b", r"\bff\b.{0,20}aide",
    r"sans diplome", r"non diplome", r"equivalent as",
]
DIPLOMA_REQUIRED_PATTERNS = [
    r"(diplome|deas|dpas|de as|d\.e\.a\.s).{0,40}(obligatoire|exige|indispensable|requis|imperatif)",
    r"(titulaire|detenteur|detentrice|etre titulaire) (du|d'un|d un|de) (deas|dpas|diplome d.?etat|diplome d.?aide)",
    r"(obligatoirement|imperativement) (titulaire|diplome)",
    r"aide[- ]soignant(e)?s? diplome(e)?s?",
]
# "AS DE" / "Aide-soignant DE" : sigle en majuscules dans le titre d'origine (≠ "aide-soignant de nuit")
_TITLE_DE = re.compile(r"\b(AS|[Aa]ide[- ]?[Ss]oignant(e)?s?)\s+D\.?E\b")
BEGINNER_PATTERNS = [
    r"debutant(e|s)? (accepte|bienvenu|possible)", r"sans experience", r"premiere experience",
    r"formation (assuree|interne|en interne)", r"non diplome(e)? accepte", r"diplome ou (en cours|faisant)",
    r"etudiant(e)?s? (infirmier|ifsi)",
]


def _any(patterns: list[str], text: str) -> str:
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(0)
    return ""


@dataclass
class Signals:
    ff: bool
    ff_match: str
    diploma_required: bool
    diploma_match: str
    beginner_ok: bool
    beginner_match: str
    shift: str
    contract: str
    part_time: bool


def extract_signals(title: str, description: str, contract_raw: str = "") -> Signals:
    t, d = fold(title), fold(description)[:6000]
    blob = f"{t} \n {d}"
    ff_m = _any(FF_PATTERNS, blob)
    beg_m = _any(BEGINNER_PATTERNS, blob)
    dip_m = _any(DIPLOMA_REQUIRED_PATTERNS, blob)
    if not dip_m and _TITLE_DE.search(title or ""):
        dip_m = _TITLE_DE.search(title).group(0)
    if not dip_m:
        # Sigle "DE" mis en minuscules par certains sites : "Aide-Soignant de CDI", "Aide-Soignant de de Nuit"
        m = re.search(r"soignant(e)?s? de (cdi|cdd|en |de |referent|interim)", t)
        dip_m = m.group(0) if m else ""
    # "diplômé ou faisant fonction" → pas un vrai diplôme exigé
    diploma = bool(dip_m) and not ff_m
    return Signals(
        ff=bool(ff_m), ff_match=ff_m,
        diploma_required=diploma, diploma_match=dip_m if diploma else "",
        beginner_ok=bool(beg_m), beginner_match=beg_m,
        shift=shift_kind(title, description),
        contract=contract_kind(contract_raw) or contract_kind(title),
        part_time=bool(re.search(r"temps partiel|parttime|part-time|mi-temps|\b\d{2}\s?%", f"{fold(contract_raw)} {t}")),
    )


def is_relevant(title: str, description: str = "") -> bool:
    t = fold(title)
    blob = f"{t} {fold(description)[:2000]}"
    relevant = ("aide-soignant", "aide soignant", "aide-soignante", "aide soignante", "as ff", "asff", "faisant fonction", "agent de soin")
    if not any(k in blob for k in relevant):
        return False
    if any(k in t for k in relevant):
        return True
    excluded = ("auxiliaire de vie", "aide a domicile", "agent de securite", "ingenieur", "manager", "boulangerie",
                "technicien", "kinesi", "infirmier", "medecin", "cadre de sante", "psychologue", "secretaire")
    return not any(x in t for x in excluded)


# ---------------------------------------------------------------------------
# Salaire
# ---------------------------------------------------------------------------

def parse_salary(text: str | None) -> tuple[int | None, int | None]:
    """Fourchette mensuelle brute estimée (€). '20 000 - 25 000 € / an' → (1667, 2083)."""
    s = fold(text).replace(" ", " ").replace("\xa0", " ")
    if not s:
        return None, None
    nums = [float(n.replace(" ", "").replace(",", ".")) for n in re.findall(r"\d[\d ]*(?:[.,]\d+)?", s)]
    nums = [n for n in nums if n > 0]
    if not nums:
        return None, None
    lo, hi = min(nums[:2]), max(nums[:2])
    if re.search(r"heure|hour|/ ?h\b|horaire", s) or hi < 60:
        factor = 151.67
    elif re.search(r"\ban\b|annuel|year|/ ?an", s) or hi > 12000:
        factor = 1 / 12
    elif re.search(r"jour|day", s):
        factor = 21.67
    else:
        factor = 1
    lo, hi = round(lo * factor), round(hi * factor)
    if not 500 <= hi <= 10000:
        return None, None
    return lo, hi


# ---------------------------------------------------------------------------
# Score explicable
# ---------------------------------------------------------------------------

def score_offer(
    signals: Signals,
    title: str,
    days_old: int | None,
    distance_km: float | None,
    sources_count: int = 1,
    reposted: int = 0,
    salary_max: int | None = None,
) -> tuple[int, list[str]]:
    """Score 0-100 orienté profil « aide-soignant faisant fonction, 2-3 ans d'expérience »."""
    score, reasons = 40, []

    def add(points: int, why: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{'+' if points >= 0 else ''}{points} {why}")

    if signals.ff:
        add(30, "faisant fonction mentionné")
    if signals.beginner_ok:
        add(10, "profil non diplômé / débutant accepté")
    if signals.diploma_required:
        add(-35, "diplôme d'État exigé")
    if "aidesoignant" in norm_title(title):
        add(5, "poste d'aide-soignant")
    if days_old is not None:
        if days_old <= 3:
            add(10, "publiée il y a moins de 3 jours")
        elif days_old <= 7:
            add(5, "publiée cette semaine")
        elif days_old > 30:
            add(-10, "publiée il y a plus d'un mois")
    if distance_km is not None:
        if distance_km <= 5:
            add(5, f"à {distance_km:.0f} km")
        elif distance_km > 15:
            add(-5, f"à {distance_km:.0f} km")
    if sources_count >= 2:
        add(min(8, 3 * sources_count), f"diffusée sur {sources_count} sites (recrutement actif)")
    if reposted:
        add(5, "republiée (poste difficile à pourvoir)")
    if signals.contract == "CDI":
        add(3, "CDI")
    if salary_max and salary_max >= 2100:
        add(3, "salaire affiché attractif")
    return max(0, min(100, score)), reasons


# ---------------------------------------------------------------------------
# Doublons
# ---------------------------------------------------------------------------

GENERIC_TITLE_TOKENS = {
    "aidesoignant", "faisantfonction", "diplome", "aide", "soignant", "soignante", "soins", "agent", "secteur",
    "hf", "fh", "ref", "reference", "poste", "nouveau", "nouvelle", "job", "mission", "missions",
}


def distinctive_tokens(title: str, city: str = "", company: str = "") -> set[str]:
    """Mots qui distinguent un poste d'un autre chez le même employeur (service, horaire, spécialité…)."""
    company_tokens = set(norm_company(company).split())
    tokens = {
        t for t in norm_title(title).split()
        if t not in GENERIC_TITLE_TOKENS and t not in company_tokens and t != city and len(t) > 1 and not t.isdigit()
    }
    # Quotité ("70%", "50 %") : deux postes différents chez le même employeur
    tokens.update(f"{n}pct" for n in re.findall(r"(\d{2,3})\s?%", title or ""))
    return tokens


@dataclass
class Candidate:
    title: str
    company: str
    city: str
    description: str
    contract: str
    shift: str
    source: str
    external_id: str = ""


@dataclass
class Match:
    same: bool
    confidence: int
    reason: str


def compare(a: Candidate, b: Candidate) -> Match:
    """Deux annonces décrivent-elles le même poste ?"""
    a_shift, b_shift = a.shift or shift_kind(a.title), b.shift or shift_kind(b.title)
    a_contract, b_contract = a.contract or contract_kind(a.title), b.contract or contract_kind(b.title)
    if a.city and b.city and a.city != b.city:
        return Match(False, 0, "villes différentes")
    if a_shift and b_shift and a_shift != b_shift:
        return Match(False, 0, f"horaires différents ({a_shift} / {b_shift})")
    if a_contract and b_contract and a_contract != b_contract:
        return Match(False, 0, f"contrats différents ({a_contract} / {b_contract})")

    ta, tb = norm_title(a.title), norm_title(b.title)
    title_sim = fuzz.token_set_ratio(ta, tb) if ta and tb else 0
    ca, cb = norm_company(a.company), norm_company(b.company)
    company_sim = fuzz.token_set_ratio(ca, cb) if ca and cb else None
    # Description entière (les agences partagent souvent la même introduction)
    da, db = fold(a.description)[:2500], fold(b.description)[:2500]
    desc_sim = fuzz.ratio(da, db) if len(da) > 150 and len(db) > 150 else None

    # Un grand employeur publie plusieurs postes d'aide-soignant : on compare ce qui les distingue
    xa = distinctive_tokens(a.title, a.city, a.company)
    xb = distinctive_tokens(b.title, b.city, b.company)
    if xa and xb:
        specific_sim = fuzz.token_sort_ratio(" ".join(sorted(xa)), " ".join(sorted(xb)))
        titles_compatible = specific_sim >= 75 or xa <= xb or xb <= xa
    elif xa or xb:
        titles_compatible = desc_sim is not None and desc_sim >= 85
    else:
        titles_compatible = True
    if not titles_compatible and not (desc_sim is not None and desc_sim >= 95):
        detail = " / ".join(" ".join(sorted(x)) or "générique" for x in (xa, xb))
        return Match(False, 0, f"postes distincts ({detail})")

    same_source = a.source == b.source
    if same_source:
        # Une même plateforme publie souvent plusieurs postes similaires (agences) :
        # on ne fusionne que les republications quasi identiques.
        if xa == xb and desc_sim is not None and desc_sim >= 97 and title_sim >= 95 and (company_sim is None or company_sim >= 95):
            return Match(True, 90, f"republication sur {a.source} (description identique à {desc_sim:.0f} %)")
        return Match(False, 0, "même site, annonces distinctes")

    if company_sim is not None and company_sim >= 88 and title_sim >= 80:
        conf = round(0.45 * company_sim + 0.35 * title_sim + 0.20 * (desc_sim if desc_sim is not None else title_sim))
        return Match(True, conf, f"même employeur ({company_sim:.0f} %), titre proche ({title_sim:.0f} %), même ville")
    if desc_sim is not None and desc_sim >= 90 and title_sim >= 75:
        return Match(True, round(0.6 * desc_sim + 0.4 * title_sim),
                     f"description identique à {desc_sim:.0f} %, titre proche ({title_sim:.0f} %)")
    return Match(False, 0, "trop différentes")
