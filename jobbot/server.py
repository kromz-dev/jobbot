"""
JobBot Dashboard — serveur local (API + planificateur)

Données : data/jobbot.db (SQLite) · data/config.json (paramètres)
Usage   : jobbot   → http://127.0.0.1:8765/
"""

from __future__ import annotations

import json
import re
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import db, pipeline
from .paths import CONFIG_PATH, CSV_PATH, DATA_DIR, JSON_PATH
from .paths import WEB_DIR as WEB
from .scraper import ALL_SOURCES, DEFAULT_CONFIG, normalize_config

HOST = "127.0.0.1"
PORT = 8765
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
MAX_LOGS = 800

state = {
    "running": False, "stop": False, "trigger": None,
    "progress": {"current": 0, "total": 0, "label": ""},
    "logs": [], "stats": {}, "started_at": None, "finished_at": None, "error": None, "last_summary": None,
}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Config (paramètres de recherche + planification)
# ---------------------------------------------------------------------------

SCHEDULE_DEFAULT = {"auto_run": False, "interval_h": 6, "quiet_start": 22, "quiet_end": 7}


def load_config() -> dict:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    cfg = normalize_config(raw)
    sched = {**SCHEDULE_DEFAULT, **(raw.get("schedule") or {})}
    sched["auto_run"] = bool(sched["auto_run"])
    for key, lo, hi in (("interval_h", 1, 72), ("quiet_start", 0, 23), ("quiet_end", 0, 23)):
        try:
            sched[key] = max(lo, min(hi, int(sched[key])))
        except (TypeError, ValueError):
            sched[key] = SCHEDULE_DEFAULT[key]
    cfg["schedule"] = sched
    return cfg


def save_config(raw: dict) -> dict:
    cfg = normalize_config(raw)
    cfg["schedule"] = {**SCHEDULE_DEFAULT, **(raw.get("schedule") or {})}
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_config()


def push_log(msg: str) -> None:
    with _lock:
        state["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg})
        if len(state["logs"]) > MAX_LOGS:
            state["logs"] = state["logs"][-MAX_LOGS:]


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------

def start_run(config: dict | None, trigger: str) -> bool:
    with _lock:
        if state["running"]:
            return False
        state.update(running=True, stop=False, trigger=trigger, logs=[], stats={}, error=None,
                     started_at=datetime.now().isoformat(timespec="seconds"), finished_at=None,
                     progress={"current": 0, "total": 0, "label": "Démarrage…"})
    threading.Thread(target=_run, args=(config or load_config(), trigger), daemon=True).start()
    return True


def _run(config: dict, trigger: str) -> None:
    def on_progress(cur: int, total: int, label: str) -> None:
        with _lock:
            state["progress"] = {"current": cur, "total": total, "label": label}

    def on_stat(stats: dict) -> None:
        with _lock:
            state["stats"] = stats

    def stop_flag() -> bool:
        with _lock:
            return bool(state["stop"])

    summary = None
    try:
        push_log(f"Recherche {trigger}…")
        summary = pipeline.run(config, on_log=push_log, on_progress=on_progress, on_stat=on_stat,
                               stop_flag=stop_flag, trigger=trigger)
    except Exception as e:  # noqa: BLE001 — remonté à l'interface
        push_log(f"ERREUR: {e}")
        with _lock:
            state["error"] = str(e)
    finally:
        with _lock:
            state["running"] = False
            state["finished_at"] = datetime.now().isoformat(timespec="seconds")
            state["last_summary"] = summary
            state["progress"]["label"] = "Arrêté" if state["stop"] else "Terminé"


def next_auto_run() -> datetime | None:
    sched = load_config()["schedule"]
    if not sched["auto_run"]:
        return None
    last = db.last_finished_run()
    base = datetime.fromisoformat(last["finished_at"]) if last and last.get("finished_at") else datetime.now()
    nxt = base + timedelta(hours=sched["interval_h"])
    qs, qe = sched["quiet_start"], sched["quiet_end"]

    def quiet(dt: datetime) -> bool:
        return (qs > qe and (dt.hour >= qs or dt.hour < qe)) or (qs < qe and qs <= dt.hour < qe)

    guard = 0
    while quiet(nxt) and guard < 48:
        nxt = (nxt + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        guard += 1
    return nxt


def scheduler_loop() -> None:
    while True:
        time.sleep(60)
        try:
            nxt = next_auto_run()
            if nxt and datetime.now() >= nxt:
                start_run(None, "automatique")
        except Exception as e:  # noqa: BLE001
            push_log(f"Planificateur: {e}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

ROUTE_OFFER = re.compile(r"^/api/offers/(\d+)$")
ROUTE_SPLIT = re.compile(r"^/api/listings/(\d+)/split$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(min(length, 2_000_000)))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _file(self, path: Path, ctype: str, download: bool = False) -> None:
        if not path.exists():
            self._json(404, {"error": f"{path.name} pas encore généré"})
            return
        extra = {"Content-Disposition": f'attachment; filename="{path.name}"'} if download else None
        self._send(200, path.read_bytes(), ctype, extra)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        if path in ("/", "/index.html"):
            self._file(WEB / "index.html", STATIC_TYPES[".html"])
        elif path.startswith("/static/"):
            target = (WEB / path.removeprefix("/static/")).resolve()
            if WEB.resolve() not in target.parents or target.suffix not in STATIC_TYPES:
                self._json(404, {"error": "introuvable"})
            else:
                self._file(target, STATIC_TYPES[target.suffix])
        elif path == "/api/state":
            with _lock:
                snap = {k: (list(v) if k == "logs" else dict(v) if isinstance(v, dict) else v) for k, v in state.items()}
            snap["data_version"] = db.data_version()
            nxt = next_auto_run()
            snap["next_auto_run"] = nxt.isoformat(timespec="minutes") if nxt else None
            if not snap["stats"]:
                last = db.last_finished_run()
                if last:
                    snap["stats"] = last["per_source"]
                    snap["finished_at"] = snap["finished_at"] or last["finished_at"]
            self._json(200, snap)
        elif path == "/api/offers":
            self._json(200, db.list_offers(include_inactive=query.get("active", ["0"])[0] != "1"))
        elif ROUTE_OFFER.match(path):
            offer = db.get_offer(int(ROUTE_OFFER.match(path).group(1)))
            self._json(200 if offer else 404, offer or {"error": "offre introuvable"})
        elif path == "/api/companies":
            self._json(200, db.companies())
        elif path == "/api/runs":
            self._json(200, db.list_runs())
        elif path == "/api/config":
            self._json(200, {"config": load_config(), "defaults": {**DEFAULT_CONFIG, "schedule": SCHEDULE_DEFAULT},
                             "all_sources": ALL_SOURCES})
        elif path == "/csv":
            self._file(CSV_PATH, "text/csv; charset=utf-8", download=True)
        elif path == "/json":
            self._file(JSON_PATH, "application/json; charset=utf-8", download=True)
        else:
            self._json(404, {"error": "introuvable"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/start":
            cfg = load_config()
            if body.get("config"):
                cfg = {**normalize_config(body["config"]), "schedule": cfg["schedule"]}
            if not cfg["sources"] or not cfg["cities"]:
                self._json(400, {"ok": False, "error": "Choisis au moins une source et une ville."})
                return
            self._json(200, {"ok": True, "started": start_run(cfg, "manuelle")})
        elif path == "/api/stop":
            with _lock:
                state["stop"] = True
            push_log("Demande d'arrêt…")
            self._json(200, {"ok": True})
        elif path == "/api/config":
            self._json(200, {"ok": True, "config": save_config(body.get("config") or {})})
        elif ROUTE_OFFER.match(path):
            offer = db.update_offer(int(ROUTE_OFFER.match(path).group(1)), body.get("status"), body.get("note"))
            self._json(200 if offer else 404, offer or {"error": "offre introuvable"})
        elif path == "/api/offers/bulk":
            ids = [int(i) for i in body.get("ids") or [] if str(i).isdigit()]
            for oid in ids:
                db.update_offer(oid, body.get("status"))
            self._json(200, {"ok": True, "updated": len(ids)})
        elif ROUTE_SPLIT.match(path):
            new_id = db.split_listing(int(ROUTE_SPLIT.match(path).group(1)))
            self._json(200 if new_id else 404, {"ok": bool(new_id), "offer_id": new_id})
        elif path == "/api/offers/merge":
            ok = db.merge_offers(int(body.get("keep", 0)), int(body.get("other", 0)))
            self._json(200 if ok else 400, {"ok": ok})
        else:
            self._json(404, {"error": "introuvable"})


def boot() -> None:
    db.init()
    msg = db.import_legacy(JSON_PATH, DATA_DIR / "suivi.json", DATA_DIR / "historique.json")
    if msg:
        push_log(f"Import des anciennes données : {msg}")
        print(f"Import : {msg}")


def main(port: int = PORT, open_browser: bool = True) -> None:
    boot()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    url = f"http://{HOST}:{port}/"
    server = ThreadingHTTPServer((HOST, port), Handler)
    print("=" * 50)
    print("JobBot Dashboard")
    print(f"Ouvre: {url}")
    print("Ctrl+C pour quitter")
    print("=" * 50, flush=True)
    print(f"Données: {DATA_DIR}")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — pas de navigateur (serveur, WSL)
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
        server.shutdown()


if __name__ == "__main__":
    main()
