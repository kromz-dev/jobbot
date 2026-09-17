"""
JobBot - Pipeline complet d'une recherche

  1. collecte multi-sources (scraper.scrape_all) + filtre de périmètre
  2. ingestion en base : rattachement des doublons, nouvelles offres, republications
  3. lecture des descriptions complètes des seules offres nouvelles (incrémental)
  4. offres disparues (non revues) → fermées
  5. exports CSV / JSON

Usage CLI : jobbot scrape
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db, extensions
from .paths import CONFIG_PATH, CSV_PATH, JSON_PATH
from .scraper import normalize_config, scrape_all
from .sources import ENRICHABLE, fetch_description

LogFn = Callable[[str], None]


def run_post_hooks(cfg: dict, summary: dict, log: LogFn) -> None:
    """Appelle chaque fonctionnalité branchée ; une erreur n'interrompt ni la recherche ni les autres crochets."""
    for hook in extensions.POST_RUN_HOOKS:
        try:
            hook(cfg, summary, log)
        except Exception as e:  # noqa: BLE001 — une extension ne doit jamais casser la recherche
            log(f"Extension {hook.__name__}: {str(e)[:200]}")


def run(
    config: dict | None = None,
    on_log: LogFn | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_stat: Callable[[dict], None] | None = None,
    stop_flag: Callable[[], bool] | None = None,
    trigger: str = "manuel",
) -> dict:
    cfg = normalize_config(config)
    t0 = time.monotonic()
    stats: dict = {}

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)
        else:
            print(msg)

    def keep_stats(s: dict) -> None:
        stats.clear()
        stats.update(s)
        if on_stat:
            on_stat(s)

    stopped = lambda: bool(stop_flag and stop_flag())  # noqa: E731
    db.init()
    run_id = db.start_run(cfg, trigger=trigger)
    summary: dict = {"run_id": run_id}
    try:
        listings = scrape_all(on_log=log, on_progress=on_progress, stop_flag=stop_flag, config=cfg, on_stat=keep_stats)

        if on_progress:
            on_progress(1, 1, "Rapprochement des doublons en base…")
        ing = db.ingest(listings, run_id)
        log(f"Base: {ing['listings']} annonces → {ing['new_offers']} nouvelles offres, "
            f"{ing['merged']} rattachees a une offre existante (doublons), {ing['updated']} deja connues, "
            f"{ing['reposted']} republiees")
        summary.update(ing)

        enriched = 0
        if cfg["enrich"] and cfg["max_enrich"] and not stopped():
            todo = db.offers_to_enrich(ENRICHABLE, cfg["max_enrich"])
            if todo:
                log(f"Lecture de {len(todo)} descriptions completes (offres nouvelles uniquement)…")
                with ThreadPoolExecutor(max_workers=6) as pool:
                    futures = {pool.submit(fetch_description, url): (oid, lid) for oid, lid, url in todo}
                    for i, fut in enumerate(as_completed(futures), 1):
                        if stopped():
                            for f in futures:
                                f.cancel()
                            break
                        oid, lid = futures[fut]
                        try:
                            text = fut.result()
                        except Exception:
                            text = ""
                        db.save_enrichment(oid, lid, text)
                        enriched += bool(text)
                        if on_progress and i % 5 == 0:
                            on_progress(i, len(todo), "Lecture des descriptions…")
                log(f"Descriptions: {enriched}/{len(todo)} recuperees")
        summary["enriched"] = enriched

        closed = 0
        if not stopped():
            ok_sources = [s for s, v in stats.items() if v.get("done") and not v.get("errors")]
            closed = db.close_missing(run_id, cfg["cities"], ok_sources)
            if closed:
                log(f"{closed} offres disparues (non revues 2 fois de suite) → marquees fermees")
        summary["closed"] = closed

        offers = db.list_offers(include_inactive=False)
        exported = db.export_files(CSV_PATH, JSON_PATH)
        summary.update(offers_active=len(offers), ff=sum(o["ff"] for o in offers))
        status = "arrete" if stopped() else "ok"
        db.finish_run(
            run_id, status=status, duration_s=round(time.monotonic() - t0), listings=ing["listings"],
            new_offers=ing["new_offers"], merged=ing["merged"], updated=ing["updated"], reposted=ing["reposted"],
            offers_active=len(offers), ff=summary["ff"], closed=closed, enriched=enriched, per_source=stats,
        )
        log(f"Termine: {len(offers)} offres actives dont {summary['ff']} faisant fonction · "
            f"{ing['new_offers']} nouvelles · export {exported} lignes")
        summary["status"] = status
        run_post_hooks(cfg, summary, log)
    except Exception as e:
        db.finish_run(run_id, status="erreur", error=str(e)[:500], duration_s=round(time.monotonic() - t0), per_source=stats)
        summary["status"] = "erreur"
        raise
    return summary


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    summary = run(config)
    print("\nTop 15 offres actives :")
    for i, o in enumerate(db.list_offers(include_inactive=False)[:15], 1):
        tags = " [FF]" if o["ff"] else (" [DE exigé]" if o["diploma_required"] else "")
        print(f"  {i:2}. {o['score']:3} {o['title'][:50]}{tags} | {o['hub']} | {', '.join(o['sources'])}")
    print(summary)


if __name__ == "__main__":
    main()
