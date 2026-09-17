# Classement personnalisé — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajuster le score de chaque offre selon les choix de l'utilisateur (favori, postulé… contre masqué), de façon locale, légère et **explicable** (« +7 selon tes choix : nuit, zone Muret »).

**Architecture:** `jobbot/features/ranking.py` entraîne à la volée un classifieur bayésien naïf (log-rapports de vraisemblance lissés) sur des caractéristiques lisibles des offres étiquetées en base, puis, via `extensions.offer_decorator`, remplace `score` par le score personnalisé (l'original est gardé dans `base_score`) et ajoute la raison à `score_reasons`. Le front existant affiche donc le nouveau score sans modification ; une carte « Ce que JobBot a appris » est ajoutée à la vue d'ensemble.

**Tech Stack:** Python 3.10+ (math, sqlite3), aucune dépendance nouvelle ; JavaScript vanilla ; pytest.

## Global Constraints

- **Prérequis :** plan `2026-09-17-00-points-extension.md` fusionné dans `main`.
- Branche : `feat/classement-personnalise`, créée depuis `main` à jour.
- Ne modifier aucun fichier existant hors `README.md` et `progress/JOURNAL.md`.
- Étiquettes : positives = statuts `favori`, `postule`, `relance`, `entretien`, `offre` ; négatives = `masque`. Les statuts `vu`, `refuse` et vide ne sont pas des étiquettes (`refuse` vient de l'employeur, pas d'une préférence).
- Activation : au moins **3 positives et 3 négatives**. Sinon aucun score n'est modifié.
- Ajustement borné à **±15 points**, score final borné à [0, 100].
- Une caractéristique n'est utilisée que si elle apparaît dans **au moins 2** offres étiquetées.
- Pas de ML lourd (pas de scikit-learn ni d'embeddings). `ruff check .` et `pytest -q` passent à chaque tâche.

## File Structure

| Fichier | Rôle |
|---|---|
| `jobbot/features/ranking.py` (créé) | Caractéristiques, modèle, décorateur d'offres, route `/api/ranking` |
| `jobbot/web/features/ranking.js` (créé) | Carte « Ce que JobBot a appris » |
| `tests/test_ranking.py` (créé) | Tests |

---

### Task 1: Caractéristiques et modèle explicable

**Files:**
- Create: `jobbot/features/ranking.py`
- Create: `tests/test_ranking.py`

**Interfaces:**
- Consumes: `jobbot.intel.distinctive_tokens(title, city, company) -> set[str]`, `jobbot.intel.norm_company(str) -> str`
- Produces:
  - `POSITIVE = {"favori", "postule", "relance", "entretien", "offre"}`, `NEGATIVE = {"masque"}`
  - `features(offer: dict) -> set[str]` — lit `title, company, city_norm, hub, contract, shift, ff, diploma_required, part_time, distance_km, n_sources`
  - `feature_label(feature: str) -> str`
  - `class Model` : `Model.train(labeled: list[dict]) -> Model` ; attributs `positives: int`, `negatives: int`, `active: bool`, `weights: dict[str, float]` ; méthode `adjust(offer: dict) -> tuple[int, list[str]]` (points, libellés des 3 caractéristiques les plus influentes dans le sens de l'ajustement)

- [ ] **Step 1: Tests qui échouent**

`tests/test_ranking.py` :

```python
import pytest

from jobbot.features import ranking


def offer(title="Aide-soignant H/F", company="Korian", hub="Toulouse", status="", **kw):
    base = dict(title=title, company=company, city_norm=hub.lower(), hub=hub, contract="CDI", shift="",
                ff=False, diploma_required=False, part_time=False, distance_km=4.0, n_sources=1, status=status)
    base.update(kw)
    return base


def test_features_are_readable():
    f = ranking.features(offer(title="Aide-soignant de nuit EHPAD", shift="nuit", ff=True, distance_km=12.0, n_sources=3))
    assert {"mot:nuit", "mot:ehpad", "employeur:korian", "zone:Toulouse", "contrat:CDI", "horaire:nuit",
            "faisant_fonction", "distance:>10", "multi_sites"} <= f
    assert "diplome_exige" not in f
    assert ranking.feature_label("mot:nuit") == "« nuit »"
    assert ranking.feature_label("employeur:korian") == "employeur korian"
    assert ranking.feature_label("zone:Muret") == "zone Muret"
    assert ranking.feature_label("distance:>10") == "à plus de 10 km"


def labeled_set():
    likes = [offer(title="Aide-soignant de nuit", hub="Muret", shift="nuit", status="favori", company=f"Ehpad {i}")
             for i in range(3)]
    dislikes = [offer(title="Aide-soignant de jour", hub="Toulouse", shift="jour", status="masque", company="Korian")
                for _ in range(3)]
    return likes + dislikes


def test_model_inactive_below_threshold():
    model = ranking.Model.train(labeled_set()[:5])
    assert not model.active
    assert model.adjust(offer()) == (0, [])


def test_model_learns_preferences_and_explains():
    model = ranking.Model.train(labeled_set())
    assert model.active and model.positives == 3 and model.negatives == 3
    up, why_up = model.adjust(offer(title="Aide-soignant de nuit", hub="Muret", shift="nuit", company="Autre"))
    down, why_down = model.adjust(offer(title="Aide-soignant de jour", hub="Toulouse", shift="jour", company="Korian"))
    assert 0 < up <= 15 and -15 <= down < 0
    assert any("nuit" in w for w in why_up) and len(why_up) <= 3
    assert any("Toulouse" in w or "korian" in w for w in why_down)


def test_unlabeled_statuses_ignored():
    data = labeled_set() + [offer(status="vu"), offer(status="refuse"), offer(status="")]
    model = ranking.Model.train(data)
    assert (model.positives, model.negatives) == (3, 3)


def test_rare_features_ignored():
    data = labeled_set() + [offer(title="Aide-soignant SSIAD unique", status="favori", company="Unique")]
    model = ranking.Model.train(data)
    assert "mot:ssiad" not in model.weights and "employeur:unique" not in model.weights


@pytest.mark.parametrize("n", [3, 30])
def test_adjust_is_bounded(n):
    likes = [offer(title="nuit nuit", shift="nuit", hub="Muret", status="favori") for _ in range(n)]
    dislikes = [offer(title="jour", shift="jour", hub="Toulouse", status="masque") for _ in range(n)]
    pts, _ = ranking.Model.train(likes + dislikes).adjust(offer(title="nuit", shift="nuit", hub="Muret"))
    assert -15 <= pts <= 15
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_ranking.py -q`
Expected: FAIL — `ImportError: cannot import name 'ranking'`

- [ ] **Step 3: Implémenter**

`jobbot/features/ranking.py` :

```python
"""
Classement personnalisé : JobBot apprend de tes choix (favori, postulé… contre masqué).

Modèle : bayésien naïf sur des caractéristiques lisibles (mots distinctifs du titre, employeur, zone,
contrat, horaire, faisant fonction, diplôme exigé, distance). Entraîné à la volée, 100 % local,
et chaque ajustement est expliqué par ses caractéristiques les plus influentes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from jobbot import intel

POSITIVE = {"favori", "postule", "relance", "entretien", "offre"}
NEGATIVE = {"masque"}
MIN_PER_CLASS = 3
MIN_SUPPORT = 2
MAX_ADJUST = 15


def features(o: dict) -> set[str]:
    out = {f"mot:{t}" for t in intel.distinctive_tokens(o.get("title", ""), o.get("city_norm", ""), o.get("company", ""))}
    company = intel.norm_company(o.get("company"))
    if company:
        out.add(f"employeur:{company}")
    for key, prefix in (("hub", "zone"), ("contract", "contrat"), ("shift", "horaire")):
        if o.get(key):
            out.add(f"{prefix}:{o[key]}")
    if o.get("ff"):
        out.add("faisant_fonction")
    if o.get("diploma_required"):
        out.add("diplome_exige")
    if o.get("part_time"):
        out.add("temps_partiel")
    dist = o.get("distance_km")
    if dist is not None:
        out.add("distance:<5" if dist < 5 else "distance:5-10" if dist <= 10 else "distance:>10")
    if (o.get("n_sources") or 1) >= 2:
        out.add("multi_sites")
    return out


FIXED_LABELS = {
    "faisant_fonction": "faisant fonction", "diplome_exige": "DEAS exigé", "temps_partiel": "temps partiel",
    "multi_sites": "publiée sur plusieurs sites", "distance:<5": "à moins de 5 km",
    "distance:5-10": "entre 5 et 10 km", "distance:>10": "à plus de 10 km",
}


def feature_label(f: str) -> str:
    if f in FIXED_LABELS:
        return FIXED_LABELS[f]
    kind, _, value = f.partition(":")
    return {"mot": f"« {value} »", "employeur": f"employeur {value}", "zone": f"zone {value}",
            "contrat": value, "horaire": f"de {value}"}.get(kind, f)


@dataclass
class Model:
    positives: int = 0
    negatives: int = 0
    weights: dict[str, float] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.positives >= MIN_PER_CLASS and self.negatives >= MIN_PER_CLASS

    @classmethod
    def train(cls, labeled: list[dict]) -> Model:
        pos = [features(o) for o in labeled if o.get("status") in POSITIVE]
        neg = [features(o) for o in labeled if o.get("status") in NEGATIVE]
        model = cls(positives=len(pos), negatives=len(neg))
        if not model.active:
            return model
        counts: dict[str, list[int]] = {}
        for bucket, rows in ((0, pos), (1, neg)):
            for feats in rows:
                for f in feats:
                    counts.setdefault(f, [0, 0])[bucket] += 1
        for f, (p, n) in counts.items():
            if p + n >= MIN_SUPPORT:
                # log-rapport de vraisemblance lissé (Laplace)
                model.weights[f] = math.log((p + 1) / (len(pos) + 2)) - math.log((n + 1) / (len(neg) + 2))
        return model

    def adjust(self, offer: dict) -> tuple[int, list[str]]:
        if not self.active:
            return 0, []
        contrib = {f: self.weights[f] for f in features(offer) if f in self.weights}
        total = sum(contrib.values())
        points = round(MAX_ADJUST * math.tanh(total / 4))
        if points == 0:
            return 0, []
        same_direction = sorted((f for f in contrib if contrib[f] * points > 0), key=lambda f: -abs(contrib[f]))
        return points, [feature_label(f) for f in same_direction[:3]]
```

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest tests/test_ranking.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
ruff check . && git add jobbot/features/ranking.py tests/test_ranking.py
git commit -m "feat(ranking): modèle bayésien explicable appris des choix de l'utilisateur"
```

---

### Task 2: Décorateur d'offres, route `/api/ranking` et carte front

**Files:**
- Modify: `jobbot/features/ranking.py`
- Modify: `tests/test_ranking.py`
- Create: `jobbot/web/features/ranking.js`
- Modify: `README.md`, `progress/JOURNAL.md`

**Interfaces:**
- Consumes: `extensions.offer_decorator`, `extensions.route`, `db.connect`, `db.list_offers`, `db.update_offer`, `window.JobBot.hooks.overviewCards`, `window.JobBot.hooks.afterLoad`
- Produces:
  - `labeled_offers(path=None) -> list[dict]` (toutes les offres avec un statut, actives ou non, avec `n_sources`)
  - décorateur `personalize(offers: list[dict]) -> None` : pour chaque offre, `base_score` = score d'origine ; si ajustement ≠ 0 : `score` = borné(score + points), `score_reasons` += `"+7 selon tes choix : « nuit », zone Muret"`, `personal_adjust` = points ; sinon `personal_adjust` = 0 ; puis re-tri de la liste par `score` décroissant
  - `GET /api/ranking` → `200 {"active": bool, "positives": int, "negatives": int, "needed": {"positives": int, "negatives": int}, "likes": [{"label", "weight"}], "dislikes": [{"label", "weight"}]}` (5 maximum chacun, poids arrondis à 2 décimales)

- [ ] **Step 1: Tests qui échouent** (ajouter)

```python
from jobbot import db, extensions


def seed(tmp_db):
    run = db.start_run({})
    rows = []
    for i in range(4):
        rows.append({"apply_url": f"https://x/nuit{i}", "source": "Hellowork", "title": f"Aide-soignant de nuit EHPAD {i}",
                     "company": f"Ehpad {i}", "location": "Muret - 31", "search_hub": "Muret"})
        rows.append({"apply_url": f"https://x/jour{i}", "source": "Indeed", "title": f"Aide-soignant de jour clinique {i}",
                     "company": "Korian", "location": "Toulouse - 31", "search_hub": "Toulouse"})
    db.ingest(rows, run)
    offers = {o["listings"][0]["url"]: o["id"] for o in db.list_offers()}
    for i in range(3):
        db.update_offer(offers[f"https://x/nuit{i}"], status="favori")
        db.update_offer(offers[f"https://x/jour{i}"], status="masque")
    return offers


def test_personalize_decorates_list_offers(tmp_db):
    offers = seed(tmp_db)
    by_url = {o["listings"][0]["url"]: o for o in db.list_offers()}
    fresh_night, fresh_day = by_url["https://x/nuit3"], by_url["https://x/jour3"]
    assert fresh_night["personal_adjust"] > 0 and fresh_night["score"] == min(100, fresh_night["base_score"] + fresh_night["personal_adjust"])
    assert fresh_day["personal_adjust"] < 0
    assert any("selon tes choix" in r for r in fresh_night["score_reasons"])
    scores = [o["score"] for o in db.list_offers()]
    assert scores == sorted(scores, reverse=True)
    assert len(offers) == 8


def test_no_change_when_inactive(tmp_db):
    run = db.start_run({})
    db.ingest([{"apply_url": "https://x/1", "source": "Hellowork", "title": "Aide-soignant", "company": "A",
                "location": "Pamiers - 09", "search_hub": "Pamiers"}], run)
    o = db.list_offers()[0]
    assert o["personal_adjust"] == 0 and o["score"] == o["base_score"]
    assert not any("selon tes choix" in r for r in o["score_reasons"])


def test_ranking_route(tmp_db):
    seed(tmp_db)
    route, m = extensions.find_route("GET", "/api/ranking")
    status, payload = route.handler(None, m, {}, {})
    assert status == 200 and payload["active"] is True
    assert payload["positives"] == 3 and payload["negatives"] == 3
    assert payload["needed"] == {"positives": 0, "negatives": 0}
    assert any("nuit" in item["label"] for item in payload["likes"])
    assert len(payload["likes"]) <= 5 and all(item["weight"] > 0 for item in payload["likes"])
    assert all(item["weight"] < 0 for item in payload["dislikes"])
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_ranking.py -q`
Expected: FAIL — `KeyError: 'personal_adjust'`

- [ ] **Step 3: Implémenter** (ajouter à `jobbot/features/ranking.py`)

Import : `from jobbot import db, extensions, intel` (remplace `from jobbot import intel`).

```python
LABELED_SQL = """
SELECT o.title, o.company, o.city_norm, o.hub, o.contract, o.shift, o.ff, o.diploma_required, o.part_time,
       o.distance_km, o.status,
       (SELECT COUNT(DISTINCT l.source) FROM listings l WHERE l.offer_id = o.id) AS n_sources
FROM offers o WHERE o.status != ''
"""


def labeled_offers(path=None) -> list[dict]:
    with db.connect(path) as c:
        return [dict(r) for r in c.execute(LABELED_SQL)]


@extensions.offer_decorator
def personalize(offers: list[dict]) -> None:
    model = Model.train(labeled_offers())
    for o in offers:
        o["base_score"] = o["score"]
        o["personal_adjust"] = 0
        if not model.active:
            continue
        points, why = model.adjust({**o, "n_sources": len(o.get("sources") or [])})
        if points:
            o["personal_adjust"] = points
            o["score"] = max(0, min(100, o["score"] + points))
            o["score_reasons"] = [*o["score_reasons"], f"{points:+d} selon tes choix : {', '.join(why)}"]
    offers.sort(key=lambda o: (-o["score"], -o["id"]))


@extensions.route("GET", r"/api/ranking")
def _route_ranking(req, match, query, body):
    model = Model.train(labeled_offers())
    ranked = sorted(model.weights.items(), key=lambda kv: kv[1])
    return 200, {
        "active": model.active,
        "positives": model.positives,
        "negatives": model.negatives,
        "needed": {"positives": max(0, MIN_PER_CLASS - model.positives), "negatives": max(0, MIN_PER_CLASS - model.negatives)},
        "likes": [{"label": feature_label(f), "weight": round(w, 2)} for f, w in reversed(ranked) if w > 0][:5],
        "dislikes": [{"label": feature_label(f), "weight": round(w, 2)} for f, w in ranked if w < 0][:5],
    }
```

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Carte front**

`jobbot/web/features/ranking.js` :

```javascript
/* Classement personnalisé : ce que JobBot a appris des choix de l'utilisateur */
(() => {
  const J = window.JobBot;
  let info = null;
  J.hooks.afterLoad.push(async () => { try { info = await J.api("/api/ranking"); } catch { info = null; } });

  J.hooks.overviewCards.push((container) => {
    if (!info) return;
    const list = (items, cls) => items.map(i => `<span class="badge ${cls}">${J.esc(i.label)}</span>`).join(" ") || `<span class="muted">—</span>`;
    const body = info.active
      ? `<p style="margin:0 0 8px">Les scores des offres sont ajustés (±15 points au plus) selon tes choix. Le détail d'une offre indique pourquoi.</p>
         <p style="margin:0 0 6px"><strong>Tu privilégies :</strong> ${list(info.likes, "b-ok")}</p>
         <p style="margin:0"><strong>Tu écartes :</strong> ${list(info.dislikes, "b-de")}</p>`
      : `<p style="margin:0">Pas encore assez de choix pour personnaliser le classement. Il manque
         <strong>${info.needed.positives}</strong> offre(s) en favori ou postulée(s) et
         <strong>${info.needed.negatives}</strong> offre(s) masquée(s).</p>`;
    container.insertAdjacentHTML("beforeend", `<div class="card">
      <div class="card-head"><div><h3>Ce que JobBot a appris</h3>
      <p class="card-sub">${info.positives} offre(s) retenue(s) · ${info.negatives} écartée(s)</p></div></div>${body}</div>`);
  });
})();
```

- [ ] **Step 6: Vérifier dans le navigateur**

Run: `python3 -m jobbot serve --no-browser`, ouvrir la vue d'ensemble.
Expected: carte « Ce que JobBot a appris » indiquant ce qui manque. Mettre 3 offres de nuit en « Favori » et 3 offres de jour en « Masquée » depuis l'onglet Offres, recharger : la carte liste « « nuit » »/« de nuit » dans *Tu privilégies* ; une autre offre de nuit a un score plus élevé et son détail contient « +N selon tes choix ». Remettre les statuts d'origine ensuite si c'étaient des essais.

- [ ] **Step 7: Documentation et commit**

`README.md`, section « Score », ajouter à la fin du paragraphe :

```markdown
**Personnalisation :** dès 3 offres retenues (favori, postulé…) et 3 masquées, JobBot apprend tes préférences
(horaires, zones, employeurs, mots du titre) et ajuste le score de ±15 points au plus, avec la raison affichée.
```

`progress/JOURNAL.md` : `| 2026-09-17 | Plan 03 — Classement personnalisé | ✅ |`

```bash
python3 -m pytest -q && ruff check .
git add jobbot/features/ranking.py jobbot/web/features/ranking.js tests/test_ranking.py README.md progress/JOURNAL.md
git commit -m "feat(ranking): score personnalisé dans les offres et carte « Ce que JobBot a appris »"
```
