"""
Points d'extension. Une fonctionnalité (jobbot/features/<nom>.py) s'y enregistre à l'import :

    from jobbot import extensions

    @extensions.route("GET", r"/api/prospects")
    def list_prospects(req, match, query, body):
        return 200, [...]

Le serveur charge automatiquement toutes les fonctionnalités au démarrage (load_features).
Ce module ne doit importer aucun autre module de jobbot (évite les imports circulaires).
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern
    handler: Callable


ROUTES: list[Route] = []
SCHEMAS: list[str] = []
MIGRATIONS: list[tuple[str, str]] = []
POST_RUN_HOOKS: list[Callable] = []
OFFER_DECORATORS: list[Callable[[list[dict]], None]] = []
REGISTRIES = ["ROUTES", "SCHEMAS", "MIGRATIONS", "POST_RUN_HOOKS", "OFFER_DECORATORS"]


def route(method: str, pattern: str):
    """Enregistre un handler HTTP : handler(req, match, query, body) -> (status, payload JSON)."""
    def deco(fn: Callable) -> Callable:
        ROUTES.append(Route(method.upper(), re.compile(pattern), fn))
        return fn
    return deco


def find_route(method: str, path: str) -> tuple[Route, re.Match] | None:
    for r in ROUTES:
        if r.method == method.upper():
            match = r.pattern.fullmatch(path)
            if match:
                return r, match
    return None


def schema(sql: str) -> None:
    """SQL idempotent (CREATE TABLE IF NOT EXISTS …) exécuté à chaque db.init()."""
    if sql not in SCHEMAS:
        SCHEMAS.append(sql)


def migration(name: str, sql: str) -> None:
    """SQL non idempotent (ALTER TABLE …) exécuté une seule fois, mémorisé dans la table migrations."""
    if name not in {n for n, _ in MIGRATIONS}:
        MIGRATIONS.append((name, sql))


def post_run(fn: Callable) -> Callable:
    """fn(cfg, summary, log) appelé à la fin de chaque recherche réussie ou arrêtée."""
    POST_RUN_HOOKS.append(fn)
    return fn


def offer_decorator(fn: Callable[[list[dict]], None]) -> Callable[[list[dict]], None]:
    """fn(offers) enrichit en place la liste renvoyée par db.list_offers()."""
    OFFER_DECORATORS.append(fn)
    return fn


def load_features() -> list[str]:
    """Importe chaque module de jobbot.features (hors noms commençant par « _ »)."""
    import jobbot.features as pkg

    names = []
    for mod in pkgutil.iter_modules(pkg.__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"jobbot.features.{mod.name}")
            names.append(mod.name)
    return sorted(names)
