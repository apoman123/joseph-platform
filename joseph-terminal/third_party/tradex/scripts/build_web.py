"""Build the public TradeX web beta (static site for Vercel, no server).

The web version is the SAME hub pages. The build copies them into web_dist/ and adds hub/web/ (browser feeds + a shim
that answers the pages' /api/... calls in the browser). The desktop pages are never edited for the web.

  python scripts/build_web.py --refresh-lists   (laptop only) re-dump the market lists into hub/web/data/
  python scripts/build_web.py                   copy + inject + personal-data scan -> web_dist/  (no network)

Lists are made on the laptop, not on Vercel: Vercel builds run in the US, where Binance.com and Bybit refuse requests,
so a list made there would be empty. The dump runs a clean PUBLIC copy of the server (no private/, no .env, no caches)
on spare ports and refuses any remote/local MT5 row. Stdlib only, so it also runs on Vercel's build machine.
"""
import ast, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUB = os.path.join(ROOT, "hub")
WEB = os.path.join(HUB, "web")
DATA = os.path.join(WEB, "data")
OUT = os.path.join(ROOT, "web_dist")
WEB_FEEDS = {"binance", "bybit", "deriv", "coinbase", "kraken", "okx"}
PAGES = ("hub.html", "vela.html", "lightweight.html", "klinechart.html", "index.html")
# Desktop-only scripts left out of the web copy (they need the server: order flow, backend log terminal).
DESKTOP_ONLY = re.compile(r'<script src="(log_panel)\.js"></script>\n?')   # order flow runs in the browser (web/orderflow_web.js)
STATIC = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(?:html|js|css|png|svg|ico|woff|woff2)$")


def refresh_lists():
    port = 8793
    tmp = tempfile.mkdtemp(prefix="tx_lists_", dir=os.path.join(ROOT, ".."))   # beside the repo (D:), not C:
    try:
        hub = os.path.join(tmp, "hub")
        os.makedirs(os.path.join(hub, "feeds"))
        for f in os.listdir(HUB):                     # code only: no caches, user data, logs or backups
            if f.endswith(".py"):
                shutil.copy2(os.path.join(HUB, f), hub)
        for f in os.listdir(os.path.join(HUB, "feeds")):
            if f.endswith(".py"):
                shutil.copy2(os.path.join(HUB, "feeds", f), os.path.join(hub, "feeds"))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("REMOTE_MT5", "ARM_", "TELEGRAM"))}
        env.update(HUB_HTTP_PORT=str(port), HUB_WS_PORT=str(port + 1), LOCAL_MT5="0", REMOTE_MT5_SSH_HOST="",
                   TRADEX_DATA=os.path.join(tmp, "data"), PYTHONDONTWRITEBYTECODE="1")
        log = open(os.path.join(tmp, "server.log"), "w")
        p = subprocess.Popen([sys.executable, os.path.join(hub, "server.py")], cwd=hub, env=env, stdout=log, stderr=subprocess.STDOUT)
        get = lambda path: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30).read())
        try:
            for _ in range(90):                       # wait until every public list has loaded (max ~3 min)
                time.sleep(2)
                if p.poll() is not None:
                    raise SystemExit("server stopped: " + open(os.path.join(tmp, "server.log")).read()[-800:])
                try:
                    s = get("/api/status")
                except Exception:
                    continue
                if all(s.get(k, {}).get("ok") for k in ("binance", "bybit", "deriv")) and len(get("/api/symbols")["symbols"]) > 500:
                    break
            else:
                raise SystemExit(f"lists did not load in time: {s}")
            time.sleep(4)
            syms, inst = get("/api/symbols"), get("/api/instruments")
        finally:
            p.terminate(); p.wait(10); log.close()
    finally:
        for _ in range(5):                            # Windows may hold the log file a moment after the server exits
            shutil.rmtree(tmp, ignore_errors=True)
            if not os.path.exists(tmp):
                break
            time.sleep(1)

    # Web copy: public feeds only; Bybit mark/index price charts dropped (a third of the rows, rarely used).
    keep = lambda t, src: src in WEB_FEEDS and not t.endswith((".MARK", ".INDEX"))
    rows = [x for x in syms["symbols"] if keep(x["ticker"], x["source"])]
    for i in inst["instruments"]:
        i["brokers"] = {b: [o for o in opts if keep(o["ticker"], b)] for b, opts in i["brokers"].items() if b in WEB_FEEDS}
        i["brokers"] = {b: o for b, o in i["brokers"].items() if o}
    inst["instruments"] = [i for i in inst["instruments"] if i["brokers"]]
    # Name and category from Deriv when it lists the market ("Gold/USD", commodity), not from the first crypto
    # exchange ("Bybit Perp - XAU/USDT", crypto). The desktop takes them from the remote MT5 broker instead.
    dname = {x["ticker"]: x for x in rows if x["source"] == "deriv"}
    dcat = {"commodity": "commodity", "forex": "forex", "indices": "index", "crypto": "crypto"}
    for i in inst["instruments"]:
        d = dname.get(((i["brokers"].get("deriv") or [{}])[0]).get("ticker"))
        if d:
            i["name"] = d["name"].split(" (")[0]
            i["cat"] = dcat.get(d["group"], i["cat"])
    inst["brokers"] = [b for b in inst["brokers"] if b["id"] in WEB_FEEDS]
    bad = [x for x in rows if x["source"] in ("arm", "mt5")] + [b for b in inst["brokers"] if b["id"] not in WEB_FEEDS]
    if bad or {b["id"] for b in inst["brokers"]} != WEB_FEEDS:
        raise SystemExit(f"refusing lists: unexpected brokers {inst['brokers']} / rows {bad[:3]}")
    # Search words ("gold", "nasdaq") come from the server's own table, so the web search ranks like the desktop one.
    tree = ast.parse(open(os.path.join(HUB, "server.py"), encoding="utf-8").read())
    inst["aliases"] = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                           and getattr(n.targets[0], "id", "") == "SEARCH_ALIASES")
    inst["made"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    os.makedirs(DATA, exist_ok=True)
    json.dump({"symbols": rows, "mt5": "off", "arm": "off", "ws_port": 8765}, open(os.path.join(DATA, "symbols.json"), "w"), separators=(",", ":"))
    json.dump(inst, open(os.path.join(DATA, "instruments.json"), "w"), separators=(",", ":"))
    print(f"lists: {len(rows)} charts, {len(inst['instruments'])} instruments, brokers {[b['id'] for b in inst['brokers']]}")


# ---------------- personal-data scan (the build fails on any hit) ----------------
# Generic leak patterns. Personal ones (server names, account numbers, emails) live in the git-ignored
# private/web_scan_patterns.txt, one regex per line, and are added when that file exists (the owner's laptop).
LEAKS = [r"(?<![\w.])(?!127\.0\.0\.1(?![\w.]))(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", r"\b\d{9,10}:[A-Za-z0-9_-]{30,}\b",
         r"[A-Za-z]:\\?Users\\?[A-Za-z]", r"/home/[a-z]", r"/mnt/", r"(?<![\w)?.])\.env\b(?!\.example)",
         r"BEGIN [A-Z ]*PRIVATE KEY", r"\b[A-Za-z0-9._%+-]+@gmail\.com\b"]
_PRIV_PAT = os.path.join(ROOT, "private", "web_scan_patterns.txt")
if os.path.isfile(_PRIV_PAT):
    LEAKS += [l.strip() for l in open(_PRIV_PAT, encoding="utf-8") if l.strip() and not l.startswith("#")]
ALLOWED = ["github.com/saifullahshafin/open-source-tradingview"]   # the AGPL source link, on purpose


def scan(folder):
    hits = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".vercel"]
        for f in files:
            path = os.path.join(base, f)
            text = open(path, encoding="utf-8", errors="replace").read()
            for a in ALLOWED:
                text = text.replace(a, "")
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            for pat in LEAKS:
                if pat == LEAKS[0] and (rel == "vela.bundle.js" or rel.startswith("vendor/")):
                    continue   # generic IP check: minified third-party SVG paths look like IPs (1.8.35.35); exact IPs still checked
                for m in re.finditer(pat, text):
                    hits.append(f"{os.path.relpath(path, folder)}: {pat} -> ...{text[max(0, m.start() - 40): m.end() + 40]!r}...")
    return hits


def build():
    for f in ("symbols.json", "instruments.json"):
        if not os.path.isfile(os.path.join(DATA, f)):
            raise SystemExit(f"missing hub/web/data/{f}: run with --refresh-lists on the laptop first")
    link = os.path.join(OUT, ".vercel")               # manual deploys upload ONLY web_dist/; keep its Vercel project link
    keep = os.path.join(ROOT, ".vercel_link_tmp")
    if os.path.isdir(link):
        shutil.rmtree(keep, ignore_errors=True); shutil.move(link, keep)
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(os.path.join(OUT, "vendor"))
    if os.path.isdir(keep):
        shutil.move(keep, link)
    for f in os.listdir(HUB):                          # same allow-list as the desktop server's static files
        if STATIC.match(f) and ".bak" not in f and os.path.isfile(os.path.join(HUB, f)):
            shutil.copy2(os.path.join(HUB, f), OUT)
    for f in os.listdir(os.path.join(HUB, "vendor")):
        if STATIC.match(f):
            shutil.copy2(os.path.join(HUB, "vendor", f), os.path.join(OUT, "vendor"))
    shutil.copytree(WEB, os.path.join(OUT, "web"), ignore=shutil.ignore_patterns("*.py", "*.sql", "__pycache__", "*.bak*"))
    head = '<script src="web/feeds_web.js"></script><script src="web/orderflow_web.js"></script><script src="web/api_shim.js"></script><script src="web/auth_web.js"></script>'
    for page in PAGES:
        p = os.path.join(OUT, page)
        html = DESKTOP_ONLY.sub("", open(p, encoding="utf-8").read())
        if page == "index.html":                        # the web entry = the sign-in page itself, at "/" (clean address)
            html = root_signin()
        else:
            i = html.find("<head>")
            if i < 0:
                raise SystemExit(f"{page}: no <head>")
            html = html[: i + 6] + head + '<meta name="viewport" content="width=device-width, initial-scale=1">' + html[i + 6:]
        open(p, "w", encoding="utf-8", newline="\n").write(html)
    patch_candles(os.path.join(OUT, "vela.bundle.js"))
    # Old sign-in address forwards to "/" (bookmarks keep working on every host).
    open(os.path.join(OUT, "web", "signin.html"), "w", encoding="utf-8", newline="\n").write(
        '<!doctype html><html><head><meta charset="utf-8"><title>TradeX</title><meta http-equiv="refresh" content="0; url=/' + '">'
        '<link rel="canonical" href="/"></head><body style="background:#0a0a0b"><a href="/" style="color:#e8ecee">Open TradeX</a></body></html>')
    # Cloudflare Pages: same headers as vercel.json + old addresses forward (Pages itself drops ".html": /hub.html -> /hub).
    open(os.path.join(OUT, "_headers"), "w", encoding="utf-8", newline="\n").write(
        "/web/data/*\n  Cache-Control: public, max-age=3600\n/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n")
    open(os.path.join(OUT, "_redirects"), "w", encoding="utf-8", newline="\n").write(
        "/web/signin.html / 301\n/web/signin / 301\n/index / 301\n")
    # Unknown address: a real 404 page (Cloudflare Pages would otherwise answer every unknown path with the front page).
    open(os.path.join(OUT, "404.html"), "w", encoding="utf-8", newline="\n").write(
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Page not found - TradeX</title><link rel="icon" type="image/png" href="/web/brand/icon-32.png"><style>'
        'html,body{margin:0;height:100%;background:#0a0a0b;color:#b4bcbf;font:15px/1.5 "Segoe UI",system-ui,sans-serif}'
        'main{height:100%;display:grid;place-items:center;text-align:center;padding:24px;box-sizing:border-box}'
        'img{width:64px;height:64px;border-radius:14px}h1{color:#e8ecee;font-size:24px;margin:18px 0 6px}'
        'a{display:inline-block;margin-top:18px;padding:10px 22px;border-radius:10px;background:#e8ecee;color:#0a0a0b;text-decoration:none;font-weight:600}'
        '</style></head><body><main><div><img src="/web/brand/icon-64.png" alt=""><h1>Page not found</h1>'
        '<div>This address does not exist on TradeX.</div><a href="/hub">Open the charts</a></div></main></body></html>')
    for f in ("LICENSE", "THIRD_PARTY_NOTICES.md", "NOTICE", "TRADEMARK.md"):
        if os.path.isfile(os.path.join(ROOT, f)):
            shutil.copy2(os.path.join(ROOT, f), os.path.join(OUT, f + (".txt" if f in ("LICENSE", "NOTICE") else "")))
    source_stamp()
    # Static-only config for `cd web_dist && vercel deploy` (no build there; headers same as the repo's vercel.json).
    cfg = json.load(open(os.path.join(ROOT, "vercel.json")))
    for k in ("buildCommand", "outputDirectory", "installCommand"):
        cfg.pop(k, None)
    cfg["cleanUrls"] = True                             # same clean addresses as Cloudflare: /hub, no ".html"
    json.dump(cfg, open(os.path.join(OUT, "vercel.json"), "w"), indent=2)
    hits = scan(OUT)
    if hits:
        print("PERSONAL-DATA SCAN FAILED:\n  " + "\n  ".join(hits[:40]))
        raise SystemExit(1)
    n = sum(len(fs) for _, _, fs in os.walk(OUT))
    print(f"web_dist: {n} files, personal-data scan clean")


REPO_URL = "https://github.com/saifullahshafin/open-source-tradingview"


def source_stamp():
    """AGPL: the web version must match public source. Write web_dist/SOURCE.txt (which commit it was built from)
    and warn loudly when the build includes uncommitted files, i.e. code that is not on GitHub yet."""
    sha = os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("CF_PAGES_COMMIT_SHA") or ""
    dirty = []
    try:
        if not sha:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                                 timeout=20).stdout.strip()
        st = subprocess.run(["git", "status", "--porcelain", "--", "hub", "scripts", "LICENSE", "NOTICE",
                             "THIRD_PARTY_NOTICES.md", "TRADEMARK.md"], cwd=ROOT, capture_output=True, text=True,
                            timeout=20).stdout
        dirty = [ln[3:] for ln in st.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    lines = ["TradeX web version - source code", f"Repository: {REPO_URL}",
             f"Built from commit: {sha or 'unknown'}" + (f"  ({REPO_URL}/tree/{sha})" if sha else ""),
             f"Built (UTC): {time.strftime('%Y-%m-%d %H:%M', time.gmtime())}"]
    if dirty:
        lines.append(f"Includes {len(dirty)} changed file(s) not yet committed; they are published with the next push:")
        lines += ["  " + d for d in dirty]
        print("WARNING (AGPL): this web build contains changes that are not on GitHub yet:\n  " + "\n  ".join(dirty[:20])
              + "\n  Commit and push them before or right after you deploy, so the web version matches its public source.")
    open(os.path.join(OUT, "SOURCE.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")


def patch_candles(path):
    """TradeX candle look (Saifullah 2026-11-25: Matte 3D + Lift), web build only; the desktop bundle stays as built.
    Each candle body gets a light left edge and a darker right edge (the GPU blends them across = soft rounded matte),
    and a soft dark shadow just below-right (lift). Thin candles (zoomed far out) stay flat."""
    b = open(path, encoding="utf-8").read()
    old = "e.rect(x.bodyX,E,x.bodyW,M,T)"
    if b.count(old) != 1:
        raise SystemExit("vela.bundle.js: candle body call not found once (Vela changed?); candle patch not applied")
    # Look chosen in Settings > Candles (theme.js sets window.__txCandle): flat | lift | matte | matte_lift (default).
    new = ("(x.bodyW>=3&&(window.__txCandle||'matte_lift')!=='flat'?("
           "((window.__txCandle||'matte_lift')!=='matte'&&e.rect(x.bodyX+1,E+2,x.bodyW,M,[0,0,0,.42*T[3]])),"
           "((window.__txCandle||'matte_lift')==='lift'?e.rect(x.bodyX,E,x.bodyW,M,T):"
           "e.quad4(x.bodyX,E,__txL(T),x.bodyX+x.bodyW,E,__txD(T),x.bodyX+x.bodyW,E+M,__txD(T),x.bodyX,E+M,__txL(T))))"
           ":e.rect(x.bodyX,E,x.bodyW,M,T))")
    helpers = "var __txL=c=>[c[0]+(1-c[0])*.14,c[1]+(1-c[1])*.14,c[2]+(1-c[2])*.14,c[3]],__txD=c=>[c[0]*.62,c[1]*.62,c[2]*.62,c[3]];" + chr(10)
    open(path, "w", encoding="utf-8", newline=chr(10)).write(helpers + b.replace(old, new, 1))


def root_signin():
    """hub/web/signin.html rewritten to be served at the site root "/" (its files stay under web/)."""
    s = open(os.path.join(WEB, "signin.html"), encoding="utf-8").read()
    for a, b in (('"brand/', '"web/brand/'), ('"../hub.html"', '"/hub"'),   # '"brand/' also covers url("brand/
                 ('"auth_config.json"', '"web/auth_config.json"'), ('src="glitch.js"', 'src="web/glitch.js"'),
                 ('location.origin + "/hub.html"', 'location.origin + "/hub"')):
        if a not in s:
            raise SystemExit(f"signin.html: expected {a!r} (root sign-in rewrite)")
        s = s.replace(a, b)
    left = [x for x in ('"brand/', '../hub.html', '"auth_config.json"', '"glitch.js"') if x in s]
    if left:
        raise SystemExit(f"signin.html: paths not rewritten for the root page: {left}")
    return s


if __name__ == "__main__":
    refresh_lists() if "--refresh-lists" in sys.argv else build()
