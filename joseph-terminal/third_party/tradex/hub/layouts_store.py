"""TradeX saved layouts and indicator templates (2026-11-23).

One JSON file, user_data/layouts.json: {"layouts": {id: doc}, "templates": {id: doc}}.
A LAYOUT is everything on screen (market, timeframe, grid, theme and candle colours, indicators with settings, drawings
per symbol, Vela chart settings). A TEMPLATE is only an indicator set, dropped onto any chart (TradingView style).
Single local user for now; when TradeX goes public this becomes per-user storage behind a login (CHART_HUB.md).
"""
import json, os, re, threading, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# Installed TradeX keeps user files in the data folder (setup sets TRADEX_DATA; updates never touch it).
PATH = os.path.join(os.environ.get("TRADEX_DATA") or os.path.join(HERE, "user_data"), "layouts.json")
LOCK = threading.Lock()
MAX_DOC = 2_000_000          # one layout (drawings included) at most 2 MB
KINDS = ("layouts", "templates")


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {k: d.get(k, {}) for k in KINDS}
    except (OSError, ValueError):
        return {k: {} for k in KINDS}


def _write(d):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, PATH)       # atomic: a crash never leaves half a file


def listing():
    """Names only (no heavy documents)."""
    d = _load()
    return {k: sorted(({"id": i, "name": v.get("name", ""), "saved_at": v.get("saved_at", 0),
                        "summary": v.get("summary", "")} for i, v in d[k].items()), key=lambda x: -x["saved_at"]) for k in KINDS}


def get(kind, id_):
    return _load().get(kind, {}).get(id_)


def handle_post(path, body):
    """POST /api/layouts/save {kind, id?, name, doc} and /api/layouts/delete {kind, id}. Returns (code, obj)."""
    try:
        req = json.loads(body or b"{}")
    except ValueError:
        return 400, {"error": "bad JSON"}
    kind = req.get("kind", "layouts")
    if kind not in KINDS:
        return 400, {"error": "kind must be layouts or templates"}
    with LOCK:
        d = _load()
        if path.endswith("/delete"):
            d[kind].pop(str(req.get("id")), None)
            _write(d)
            return 200, {"ok": True}
        if path.endswith("/save"):
            name = re.sub(r"[\x00-\x1f]", "", str(req.get("name") or "").strip())[:60] or "Untitled"
            doc = req.get("doc")
            if not isinstance(doc, dict):
                return 400, {"error": "doc must be an object"}
            if len(json.dumps(doc)) > MAX_DOC:
                return 413, {"error": "layout too large (over 2 MB)"}
            id_ = str(req.get("id") or "") or uuid.uuid4().hex[:12]
            if not re.fullmatch(r"[0-9a-zA-Z_-]{1,40}", id_):
                return 400, {"error": "bad id"}
            d[kind][id_] = {"name": name, "saved_at": int(time.time()), "summary": str(req.get("summary") or "")[:120], "doc": doc}
            _write(d)
            return 200, {"ok": True, "id": id_}
    return 404, {"error": "not found"}
