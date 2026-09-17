# Candidature assistée — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Générer en un clic un message de candidature prêt à envoyer (réponse à une offre, candidature spontanée, relance), personnalisé avec le profil de l'utilisateur et adapté au cas « faisant fonction » / « DEAS exigé », à copier ou ouvrir dans la messagerie.

**Architecture:** `jobbot/features/applications.py` gère le profil (`data/profile.json`), des modèles de texte déterministes (pas d'IA générative) et les routes `/api/profile` et `/api/letters`. `jobbot/web/features/applications.js` ajoute une vue « Mon profil », un bouton « Rédiger ma candidature » dans le détail des offres (`hooks.detailExtras`) et expose `JobBot.openLetter(kind, payload)` (utilisée par la vue Établissements du plan 01 si elle est présente).

**Tech Stack:** Python 3.10+ (json, urllib.parse), JavaScript vanilla (`<dialog>`), pytest.

## Global Constraints

- **Prérequis :** plan `2026-09-17-00-points-extension.md` fusionné dans `main`. Indépendant des plans 01, 02, 03, 05 (la candidature spontanée lit la table `prospects` **si elle existe**, sinon utilise le contexte fourni).
- Branche : `feat/candidature-assistee`, créée depuis `main` à jour.
- Ne modifier aucun fichier existant hors `README.md` et `progress/JOURNAL.md`.
- Profil stocké dans `DATA_DIR / "profile.json"` (jamais versionné). Champs et types figés : `prenom` (str), `nom` (str), `telephone` (str), `email` (str), `ville` (str), `experience` (str, ex. « 2 ans et demi en EHPAD comme aide-soignante faisant fonction »), `atouts` (str), `disponibilite` (str, ex. « immédiatement »), `vehicule` (bool), `vae_en_cours` (bool).
- Un champ de profil vide apparaît dans le texte sous la forme `[prénom]`, `[téléphone]`… (jamais « None »).
- Écriture neutre et polie : formulations sans genre (« Avec une expérience de… ») ou accords doubles (« ouverte ou ouvert »).
- Aucun envoi automatique d'e-mail : l'utilisateur copie le texte ou ouvre sa messagerie (`mailto:`).
- `ruff check .` et `pytest -q` passent à chaque tâche.

## File Structure

| Fichier | Rôle |
|---|---|
| `jobbot/features/applications.py` (créé) | Profil, modèles, routes |
| `jobbot/web/features/applications.js` (créé) | Vue « Mon profil », bouton dans le détail, boîte de dialogue |
| `tests/test_applications.py` (créé) | Tests |

---

### Task 1: Profil et modèles de messages

**Files:**
- Create: `jobbot/features/applications.py`
- Create: `tests/test_applications.py`

**Interfaces:**
- Consumes: `jobbot.paths.DATA_DIR`
- Produces:
  - `PROFILE_FIELDS: dict[str, type]` (voir contraintes)
  - `load_profile(path: Path | None = PROFILE_PATH) -> dict` (valeurs par défaut si `path` vaut `None`, est absent ou illisible)
  - `save_profile(data: dict, path: Path = PROFILE_PATH) -> dict` (ne garde que les champs connus, convertit les types, tronque les str à 500 caractères)
  - `render_letter(kind: str, context: dict, profile: dict) -> dict` → `{"subject": str, "body": str}` ; `kind ∈ {"offre", "spontanee", "relance"}` sinon `ValueError`
  - contexte `offre` : `title, company, city, ff (bool), diploma_required (bool)` ; `spontanee` : `company, city` ; `relance` : `title, company, applied_on (JJ/MM/AAAA)`

- [ ] **Step 1: Tests qui échouent**

`tests/test_applications.py` :

```python
import json

import pytest

from jobbot.features import applications as app

PROFILE = {"prenom": "Camille", "nom": "Martin", "telephone": "06 12 34 56 78", "email": "camille@example.org",
           "ville": "Pamiers", "experience": "2 ans et demi en EHPAD comme aide-soignante faisant fonction",
           "atouts": "douceur, sens de l'équipe, travail de nuit", "disponibilite": "immédiatement",
           "vehicule": True, "vae_en_cours": False}


def test_profile_roundtrip_filters_and_casts(tmp_path):
    path = tmp_path / "profile.json"
    assert app.load_profile(path)["prenom"] == "" and app.load_profile(path)["vehicule"] is False
    saved = app.save_profile({**PROFILE, "vehicule": "true", "inconnu": "x", "atouts": "a" * 900}, path)
    assert "inconnu" not in saved and saved["vehicule"] is True and len(saved["atouts"]) == 500
    assert json.loads(path.read_text(encoding="utf-8"))["prenom"] == "Camille"
    path.write_text("{corrompu", encoding="utf-8")
    assert app.load_profile(path)["prenom"] == ""


def test_offer_letter_standard():
    letter = app.render_letter("offre", {"title": "Aide-soignant de nuit (H/F)", "company": "EHPAD Les Lilas",
                                         "city": "Muret", "ff": False, "diploma_required": False}, PROFILE)
    assert letter["subject"] == "Candidature — Aide-soignant de nuit (H/F)"
    body = letter["body"]
    assert body.startswith("Madame, Monsieur,")
    assert "EHPAD Les Lilas" in body and "Muret" in body
    assert "2 ans et demi en EHPAD" in body and "douceur, sens de l'équipe" in body
    assert "véhicule" in body and "immédiatement" in body
    assert body.rstrip().endswith("Camille Martin\n06 12 34 56 78 · camille@example.org")
    assert "DEAS" not in body


def test_offer_letter_diploma_required_mentions_vae_path():
    ctx = {"title": "Aide-soignant DE", "company": "Clinique", "city": "Toulouse", "ff": False, "diploma_required": True}
    assert "VAE" in app.render_letter("offre", ctx, PROFILE)["body"]
    assert "démarche de VAE est en cours" in app.render_letter("offre", ctx, {**PROFILE, "vae_en_cours": True})["body"]


def test_offer_letter_faisant_fonction_highlights_experience():
    ctx = {"title": "AS faisant fonction", "company": "EHPAD", "city": "Pamiers", "ff": True, "diploma_required": False}
    assert "faisant fonction" in app.render_letter("offre", ctx, PROFILE)["body"]


def test_spontaneous_and_followup():
    s = app.render_letter("spontanee", {"company": "EHPAD DE MIREPOIX", "city": "Mirepoix"}, PROFILE)
    assert s["subject"] == "Candidature spontanée — aide-soignante / aide-soignant"
    assert "EHPAD DE MIREPOIX" in s["body"] and "remplacements" in s["body"]
    r = app.render_letter("relance", {"title": "Aide-soignant", "company": "Korian", "applied_on": "02/09/2026"}, PROFILE)
    assert r["subject"] == "Relance — candidature Aide-soignant" and "02/09/2026" in r["body"]


def test_missing_profile_fields_are_placeholders():
    body = app.render_letter("spontanee", {"company": "", "city": ""}, app.load_profile(None))["body"]
    assert "[prénom]" in body and "[téléphone]" in body and "None" not in body
    assert "votre établissement" in body


def test_unknown_kind():
    with pytest.raises(ValueError):
        app.render_letter("poeme", {}, PROFILE)
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_applications.py -q`
Expected: FAIL — `ImportError: cannot import name 'applications'`

- [ ] **Step 3: Implémenter**

`jobbot/features/applications.py` :

```python
"""
Candidature assistée : messages prêts à envoyer (réponse à une offre, candidature spontanée, relance),
construits à partir du profil. Modèles déterministes, aucun envoi automatique.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobbot.paths import DATA_DIR

PROFILE_PATH = DATA_DIR / "profile.json"
PROFILE_FIELDS: dict[str, type] = {
    "prenom": str, "nom": str, "telephone": str, "email": str, "ville": str,
    "experience": str, "atouts": str, "disponibilite": str, "vehicule": bool, "vae_en_cours": bool,
}
PLACEHOLDERS = {"prenom": "[prénom]", "nom": "[nom]", "telephone": "[téléphone]", "email": "[e-mail]",
                "ville": "[ville]", "experience": "[votre expérience]", "atouts": "[vos qualités]",
                "disponibilite": "[disponibilité]"}
KINDS = ("offre", "spontanee", "relance")


def _cast(kind: type, value):
    if kind is bool:
        return value is True or str(value).strip().lower() in ("true", "1", "oui", "on")
    return str(value or "").strip()[:500]


def load_profile(path: Path | None = PROFILE_PATH) -> dict:
    data = {}
    if path is not None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    return {k: _cast(t, data.get(k)) for k, t in PROFILE_FIELDS.items()}


def save_profile(data: dict, path: Path = PROFILE_PATH) -> dict:
    profile = {k: _cast(t, data.get(k)) for k, t in PROFILE_FIELDS.items()}
    Path(path).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def _p(profile: dict) -> dict:
    return {k: (profile.get(k) or PLACEHOLDERS.get(k, "")) if PROFILE_FIELDS[k] is str else profile.get(k)
            for k in PROFILE_FIELDS}


def _signature(p: dict) -> str:
    return f"Cordialement,\n\n{p['prenom']} {p['nom']}\n{p['telephone']} · {p['email']}"


def _practical(p: dict) -> str:
    parts = [f"Je suis disponible {p['disponibilite']}"]
    if p["vehicule"]:
        parts.append("véhiculée ou véhiculé, je peux me déplacer facilement")
    return ", ".join(parts) + ", y compris pour des horaires de nuit, de week-end ou des remplacements."


def render_letter(kind: str, context: dict, profile: dict) -> dict:
    if kind not in KINDS:
        raise ValueError(f"type de message inconnu : {kind}")
    p = _p(profile)
    company = context.get("company") or "votre établissement"
    city = context.get("city") or ""
    where = f"{company}{f' à {city}' if city else ''}"

    if kind == "offre":
        title = context.get("title") or "aide-soignant"
        paragraphs = [
            f"Je vous propose ma candidature au poste « {title} » au sein de {where}.",
            f"Avec une expérience de {p['experience']}, je maîtrise les soins d'hygiène et de confort, "
            f"l'accompagnement des résidents dans les gestes du quotidien et la transmission aux équipes. "
            f"On me reconnaît {p['atouts']}.",
        ]
        if context.get("ff"):
            paragraphs.append("Votre annonce est ouverte aux profils faisant fonction : c'est précisément le poste que "
                              "j'occupe au quotidien, et je suis opérationnelle ou opérationnel rapidement.")
        if context.get("diploma_required"):
            paragraphs.append(
                "Ma démarche de VAE est en cours pour obtenir le DEAS, et je peux vous transmettre mon calendrier."
                if profile.get("vae_en_cours") else
                "Je souhaite obtenir le DEAS par la VAE grâce à mon expérience, et reste ouverte ou ouvert à un poste "
                "de faisant fonction ou à un contrat en apprentissage au sein de votre équipe.")
        paragraphs.append(_practical(p))
        paragraphs.append("Je serais heureuse ou heureux d'échanger avec vous lors d'un entretien.")
        return {"subject": f"Candidature — {title}", "body": "Madame, Monsieur,\n\n" + "\n\n".join(paragraphs) + "\n\n" + _signature(p)}

    if kind == "spontanee":
        paragraphs = [
            f"Je me permets de vous adresser ma candidature spontanée pour un poste d'aide-soignante ou d'aide-soignant "
            f"au sein de {where}.",
            f"J'ai une expérience de {p['experience']}. On me reconnaît {p['atouts']}.",
            "Je suis intéressée ou intéressé par un poste en CDI, en CDD ou par des remplacements, "
            "et reste ouverte ou ouvert à un poste de faisant fonction.",
            _practical(p),
            "Je me tiens à votre disposition pour un entretien ou une journée d'observation.",
        ]
        return {"subject": "Candidature spontanée — aide-soignante / aide-soignant",
                "body": "Madame, Monsieur,\n\n" + "\n\n".join(paragraphs) + "\n\n" + _signature(p)}

    title = context.get("title") or "aide-soignant"
    applied_on = context.get("applied_on") or "[date]"
    paragraphs = [
        f"Le {applied_on}, je vous ai adressé ma candidature au poste « {title} » au sein de {where}.",
        "Ce poste m'intéresse toujours vivement et je me permets de revenir vers vous pour savoir "
        "où en est le recrutement.",
        "Je reste disponible pour un entretien à votre convenance.",
    ]
    return {"subject": f"Relance — candidature {title}", "body": "Madame, Monsieur,\n\n" + "\n\n".join(paragraphs) + "\n\n" + _signature(p)}
```

Remarque : les accords doubles (« ouverte ou ouvert », « véhiculée ou véhiculé ») respectent la contrainte d'écriture neutre sans supposer le genre ; « véhiculée » contient la sous-chaîne « véhicule » attendue par le test.

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest tests/test_applications.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
ruff check . && git add jobbot/features/applications.py tests/test_applications.py
git commit -m "feat(applications): profil et modèles de messages (offre, spontanée, relance)"
```

---

### Task 2: Routes `/api/profile` et `/api/letters`

**Files:**
- Modify: `jobbot/features/applications.py`
- Modify: `tests/test_applications.py`

**Interfaces:**
- Consumes: `extensions.route`, `db.get_offer(offer_id) -> dict | None` (clés `title, company, hub, ff, diploma_required, status_history, listings`), `db.connect`
- Produces:
  - `GET /api/profile` → `200 load_profile()` ; `POST /api/profile` → `200 save_profile(body)`
  - `POST /api/letters` corps `{"kind": "offre"|"relance", "offer_id": int}` ou `{"kind": "spontanee", "siret": "…14 chiffres…"}` ou `{"kind": …, "context": {...}}` → `200 {"subject", "body", "mailto"}` ; `mailto` = `mailto:<email>?subject=…&body=…` (email de l'établissement si connu, sinon vide avant `?`) ; `404` offre/établissement introuvable ; `400` type inconnu
  - date de candidature pour `relance` : dernier passage au statut `postule` dans `status_history`, format `JJ/MM/AAAA`

- [ ] **Step 1: Tests qui échouent** (ajouter)

```python
from urllib.parse import parse_qs, urlparse

from jobbot import db, extensions


def call(method, path, body=None):
    route, match = extensions.find_route(method, path)
    return route.handler(None, match, {}, body or {})


@pytest.fixture
def profile_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "PROFILE_PATH", tmp_path / "profile.json")
    app.save_profile(PROFILE, tmp_path / "profile.json")


def test_profile_routes(profile_file):
    assert call("GET", "/api/profile")[1]["prenom"] == "Camille"
    status, saved = call("POST", "/api/profile", {**PROFILE, "prenom": "Alex"})
    assert status == 200 and saved["prenom"] == "Alex" and call("GET", "/api/profile")[1]["prenom"] == "Alex"


def test_letter_for_offer_and_followup(profile_file, tmp_db):
    run = db.start_run({})
    db.ingest([{"apply_url": "https://x/1", "source": "Hellowork", "title": "Aide-soignant de nuit (H/F)",
                "company": "EHPAD Les Lilas", "location": "Muret - 31", "search_hub": "Muret"}], run)
    oid = db.list_offers()[0]["id"]
    status, letter = call("POST", "/api/letters", {"kind": "offre", "offer_id": oid})
    assert status == 200 and "EHPAD Les Lilas" in letter["body"] and "Muret" in letter["body"]
    mail = urlparse(letter["mailto"])
    assert mail.scheme == "mailto" and mail.path == ""
    assert parse_qs(mail.query)["subject"] == ["Candidature — Aide-soignant de nuit (H/F)"]
    db.update_offer(oid, status="postule")
    status, relance = call("POST", "/api/letters", {"kind": "relance", "offer_id": oid})
    today = db.get_offer(oid)["status_history"][-1]["at"]
    assert f"{today[8:10]}/{today[5:7]}/{today[:4]}" in relance["body"]
    assert call("POST", "/api/letters", {"kind": "offre", "offer_id": 999999})[0] == 404
    assert call("POST", "/api/letters", {"kind": "poeme", "context": {}})[0] == 400


def test_letter_spontaneous_with_prospect_table(profile_file, tmp_db):
    with db.connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS prospects (siret TEXT PRIMARY KEY, name TEXT, city TEXT, email TEXT)")
        # colonnes nommées : compatible avec le schéma complet du plan 01 s'il est fusionné
        c.execute("INSERT INTO prospects (siret, name, city, email) "
                  "VALUES ('26090012100013', 'EHPAD DE MIREPOIX', 'Mirepoix', 'mr.mirepoix@wanadoo.fr')")
    status, letter = call("POST", "/api/letters", {"kind": "spontanee", "siret": "26090012100013"})
    assert status == 200 and "EHPAD DE MIREPOIX" in letter["body"]
    assert letter["mailto"].startswith("mailto:mr.mirepoix@wanadoo.fr?")
    assert call("POST", "/api/letters", {"kind": "spontanee", "siret": "00000000000000"})[0] == 404


def test_letter_spontaneous_without_prospect_table(profile_file, tmp_db):
    status, letter = call("POST", "/api/letters", {"kind": "spontanee", "context": {"company": "Clinique X", "city": "Foix"}})
    assert status == 200 and "Clinique X" in letter["body"]
```

- [ ] **Step 2: Vérifier l'échec**

Run: `python3 -m pytest tests/test_applications.py -q`
Expected: FAIL — `TypeError: cannot unpack non-iterable NoneType object`

- [ ] **Step 3: Implémenter** (ajouter à `jobbot/features/applications.py`)

Imports supplémentaires :

```python
import sqlite3
from urllib.parse import quote

from jobbot import db, extensions
```

En fin de fichier :

```python
def _mailto(to: str, letter: dict) -> str:
    return f"mailto:{to}?subject={quote(letter['subject'])}&body={quote(letter['body'])}"


def _fr_date(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso and len(iso) >= 10 else ""


@extensions.route("GET", r"/api/profile")
def _route_profile_get(req, match, query, body):
    return 200, load_profile(PROFILE_PATH)


@extensions.route("POST", r"/api/profile")
def _route_profile_save(req, match, query, body):
    return 200, save_profile(body, PROFILE_PATH)


@extensions.route("POST", r"/api/letters")
def _route_letter(req, match, query, body):
    kind = body.get("kind")
    if kind not in KINDS:
        return 400, {"error": f"type de message inconnu : {kind}"}
    context, email = dict(body.get("context") or {}), ""
    if body.get("offer_id") is not None:
        offer = db.get_offer(int(body["offer_id"]))
        if not offer:
            return 404, {"error": "offre introuvable"}
        applied = [h["at"] for h in offer["status_history"] if h["status"] == "postule"]
        context = {"title": offer["title"], "company": offer["company"], "city": offer["hub"],
                   "ff": offer["ff"], "diploma_required": offer["diploma_required"],
                   "applied_on": _fr_date(applied[-1] if applied else "")}
    elif body.get("siret"):
        try:
            with db.connect() as c:
                row = c.execute("SELECT name, city, email FROM prospects WHERE siret = ?", (str(body["siret"]),)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if not row:
            return 404, {"error": "établissement introuvable"}
        context, email = {"company": row["name"], "city": row["city"]}, row["email"] or ""
    letter = render_letter(kind, context, load_profile(PROFILE_PATH))
    return 200, {**letter, "mailto": _mailto(email, letter)}
```

- [ ] **Step 4: Lancer les tests**

Run: `python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff check . && git add jobbot/features/applications.py tests/test_applications.py
git commit -m "feat(applications): routes profil et génération de messages"
```

---

### Task 3: Vue « Mon profil », bouton dans le détail des offres et boîte de dialogue

**Files:**
- Create: `jobbot/web/features/applications.js`
- Modify: `README.md`, `progress/JOURNAL.md`

**Interfaces:**
- Consumes: `window.JobBot.{registerView, hooks.detailExtras, api, esc, toast, S, renderView}`, routes Task 2, route cœur `POST /api/offers/<id>` `{status}`
- Produces: vue `#/profil` ; `window.JobBot.openLetter(kind: "offre"|"spontanee"|"relance", payload: {offer_id?: number, siret?: string, context?: object}) -> Promise<void>`

- [ ] **Step 1: Écrire le script**

`jobbot/web/features/applications.js` :

```javascript
/* Candidature assistée : profil, messages prêts à envoyer */
(() => {
  const J = window.JobBot;
  const FIELDS = [
    ["prenom", "Prénom", "text"], ["nom", "Nom", "text"], ["telephone", "Téléphone", "tel"], ["email", "E-mail", "email"],
    ["ville", "Ville", "text"], ["disponibilite", "Disponibilité", "text"],
    ["experience", "Expérience (une phrase)", "textarea"], ["atouts", "Qualités / atouts", "textarea"],
    ["vehicule", "J'ai un véhicule", "checkbox"], ["vae_en_cours", "Ma VAE (DEAS) est en cours", "checkbox"],
  ];

  /* ---------- Boîte de dialogue ---------- */
  const dialog = document.createElement("dialog");
  dialog.className = "card";
  dialog.style.cssText = "max-width:720px;width:calc(100% - 32px);border:1px solid var(--border);padding:16px;color:var(--text);background:var(--surface)";
  dialog.innerHTML = `
    <form method="dialog" style="display:grid;gap:10px">
      <div class="card-head" style="margin:0"><div><h3 id="lt-title">Message</h3><p class="card-sub">Relis et adapte avant d'envoyer.</p></div>
        <button type="submit" class="btn-ghost btn-sm icon-btn" aria-label="Fermer">${J.icon("x")}</button></div>
      <label for="lt-subject" style="font-weight:600;font-size:.875rem">Objet</label><input type="text" id="lt-subject"/>
      <label for="lt-body" style="font-weight:600;font-size:.875rem">Message</label><textarea id="lt-body" style="min-height:320px"></textarea>
      <div class="inline">
        <button type="button" class="btn-primary btn-sm" id="lt-copy">Copier le message</button>
        <a class="btn btn-ghost btn-sm" id="lt-mail" href="#">Ouvrir ma messagerie</a>
        <button type="button" class="btn-ghost btn-sm" id="lt-applied" hidden>Marquer comme postulé</button>
      </div>
    </form>`;
  document.body.appendChild(dialog);
  let currentOffer = null;

  dialog.addEventListener("click", async (e) => {
    if (e.target.id === "lt-copy") {
      const text = `Objet : ${dialog.querySelector("#lt-subject").value}\n\n${dialog.querySelector("#lt-body").value}`;
      try { await navigator.clipboard.writeText(text); J.toast("Message copié"); }
      catch { dialog.querySelector("#lt-body").select(); document.execCommand("copy"); J.toast("Message copié"); }
    }
    if (e.target.id === "lt-applied" && currentOffer) {
      await J.api(`/api/offers/${currentOffer}`, { status: "postule" });
      const o = J.S.offers.find(x => x.id === currentOffer);
      if (o) o.status = "postule";
      J.toast("Statut : Postulé"); dialog.close(); J.renderView();
    }
  });
  dialog.addEventListener("input", () => {
    const subject = encodeURIComponent(dialog.querySelector("#lt-subject").value);
    const body = encodeURIComponent(dialog.querySelector("#lt-body").value);
    const link = dialog.querySelector("#lt-mail");
    link.href = link.href.replace(/\?.*$/, `?subject=${subject}&body=${body}`);
  });

  J.openLetter = async (kind, payload = {}) => {
    try {
      const letter = await J.api("/api/letters", { kind, ...payload });
      currentOffer = payload.offer_id ?? null;
      dialog.querySelector("#lt-title").textContent = { offre: "Répondre à l'offre", spontanee: "Candidature spontanée", relance: "Relance" }[kind];
      dialog.querySelector("#lt-subject").value = letter.subject;
      dialog.querySelector("#lt-body").value = letter.body;
      dialog.querySelector("#lt-mail").href = letter.mailto;
      dialog.querySelector("#lt-applied").hidden = kind !== "offre" || currentOffer === null;
      dialog.showModal();
      if (letter.body.includes("[prénom]")) J.toast("Complète ton profil (onglet Mon profil) pour personnaliser le message.");
    } catch (err) { J.toast("Impossible de rédiger : " + err.message); }
  };

  /* ---------- Bouton dans le détail d'une offre ---------- */
  J.hooks.detailExtras.push((o) => {
    const followUp = ["postule", "relance"].includes(o.status);
    return `<div class="inline" style="margin-top:4px">
      <button type="button" class="btn-primary btn-sm" data-letter="offre" data-offer="${o.id}">Rédiger ma candidature</button>
      ${followUp ? `<button type="button" class="btn-ghost btn-sm" data-letter="relance" data-offer="${o.id}">Rédiger une relance</button>` : ""}
    </div>`;
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-letter][data-offer]");
    if (b) J.openLetter(b.dataset.letter, { offer_id: Number(b.dataset.offer) });
  });

  /* ---------- Vue « Mon profil » ---------- */
  let profile = null;
  J.registerView({
    id: "profil", label: "Mon profil",
    render: async (section) => {
      if (!profile) profile = await J.api("/api/profile");
      section.innerHTML = `
        <h2 class="view-title">Mon profil</h2>
        <p class="view-sub">Ces informations remplissent tes messages de candidature. Elles restent sur ton ordinateur (data/profile.json).</p>
        <div class="card" style="max-width:760px"><form id="pf-form" class="form-grid" onsubmit="return false">
          ${FIELDS.map(([k, label, type]) => type === "checkbox"
            ? `<label class="check"><input type="checkbox" name="${k}" ${profile[k] ? "checked" : ""}/>${label}</label>`
            : type === "textarea"
              ? `<div class="form-row"><label for="pf-${k}">${label}</label><textarea id="pf-${k}" name="${k}" style="min-height:70px">${J.esc(profile[k])}</textarea></div>`
              : `<div class="form-row"><label for="pf-${k}">${label}</label><input id="pf-${k}" name="${k}" type="${type}" value="${J.esc(profile[k])}"/></div>`).join("")}
          <div class="inline"><button type="button" class="btn-primary" id="pf-save">Enregistrer</button>
            <button type="button" class="btn-ghost" id="pf-preview">Aperçu d'une candidature spontanée</button></div>
        </form></div>`;
    },
    bind: (section) => {
      section.addEventListener("click", async (e) => {
        const form = section.querySelector("#pf-form");
        if (!form) return;
        const data = Object.fromEntries(FIELDS.map(([k, , type]) => [k, type === "checkbox" ? form.elements[k].checked : form.elements[k].value]));
        if (e.target.id === "pf-save") { profile = await J.api("/api/profile", data); J.toast("Profil enregistré"); }
        if (e.target.id === "pf-preview") { profile = await J.api("/api/profile", data); J.openLetter("spontanee", { context: { company: "", city: profile.ville } }); }
      });
    },
  });
})();
```

- [ ] **Step 2: Vérifier dans le navigateur**

Run: `python3 -m jobbot serve --no-browser`.
Expected :
1. Onglet « Mon profil » : remplir, Enregistrer (toast), recharger → valeurs conservées ; « Aperçu » ouvre la boîte de dialogue avec le prénom et l'expérience.
2. Offres → ouvrir le détail d'une offre → « Rédiger ma candidature » : objet `Candidature — <titre>`, message avec l'employeur ; « Copier le message » (toast) ; « Ouvrir ma messagerie » ouvre le client mail ; « Marquer comme postulé » change le statut ; rouvrir le détail → bouton « Rédiger une relance » présent.
3. Si le plan 01 est fusionné : Établissements → « Rédiger » ouvre une candidature spontanée pré-adressée.
4. Touche Échap ferme la boîte ; aucune erreur console ; à 375 px la boîte tient dans l'écran.

- [ ] **Step 3: Documentation et commit**

`README.md`, section « Fonctionnalités », ajouter :

```markdown
  - *Candidature assistée* : profil local et messages prêts à envoyer (réponse à une offre, candidature spontanée, relance), adaptés aux postes « faisant fonction » et « DEAS exigé » (mention de la VAE), à copier ou ouvrir dans ta messagerie.
```

`progress/JOURNAL.md` : `| 2026-09-17 | Plan 04 — Candidature assistée | ✅ |`

```bash
python3 -m pytest -q && ruff check .
git add jobbot/web/features/applications.js README.md progress/JOURNAL.md
git commit -m "feat(applications): vue Mon profil et rédaction de candidature depuis une offre"
```
