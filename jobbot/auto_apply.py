"""
Ouverture guidée des offres : les meilleures offres non traitées, une par une.
Chaque offre ouverte passe au statut « Vue » ; réponds « p » si tu as postulé.

Usage : jobbot apply
"""

from __future__ import annotations

import webbrowser

from . import db

PROMPT = "  [Entrée] suivante · [p] j'ai postulé · [f] favori · [m] masquer · [q] quitter : "


def main(limit: int = 30) -> None:
    db.init()
    offers = [
        o for o in db.list_offers(include_inactive=False)
        if not o["status"] and not o["diploma_required"] and o["url"]
    ][:limit]
    if not offers:
        print("Aucune offre à traiter. Lance d'abord : jobbot scrape")
        return

    print(f"{len(offers)} offre(s), les mieux notées d'abord.\n")
    for i, o in enumerate(offers, 1):
        tags = " [faisant fonction]" if o["ff"] else ""
        print(f"[{i}/{len(offers)}] score {o['score']} — {o['title']}{tags}")
        print(f"  {o['company'] or '?'} · {o['location']} · {', '.join(o['sources'])}")
        for reason in o["score_reasons"][:3]:
            print(f"    {reason}")
        webbrowser.open(o["url"])
        db.update_offer(o["id"], status="vu")
        answer = input(PROMPT).strip().lower()
        if answer == "q":
            break
        status = {"p": "postule", "f": "favori", "m": "masque"}.get(answer)
        if status:
            db.update_offer(o["id"], status=status)
    print("Terminé.")


if __name__ == "__main__":
    main()
