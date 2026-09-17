"""Ligne de commande : jobbot [serve|scrape|apply]."""

from __future__ import annotations

import argparse

from . import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jobbot", description="Recherche d'emploi aide-soignant : scraping, doublons, suivi.")
    parser.add_argument("--version", action="version", version=f"jobbot {__version__}")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="tableau de bord web (par défaut)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    sub.add_parser("scrape", help="lancer une recherche complète en ligne de commande")
    sub.add_parser("apply", help="ouvrir les meilleures offres une par une")
    args = parser.parse_args(argv)

    if args.command == "scrape":
        from .pipeline import main as scrape_main

        scrape_main()
    elif args.command == "apply":
        from .auto_apply import main as apply_main

        apply_main()
    else:
        from .server import main as serve_main

        serve_main(port=getattr(args, "port", 8765), open_browser=not getattr(args, "no_browser", False))


if __name__ == "__main__":
    main()
