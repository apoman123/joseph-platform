"""TradeX setup step engine: one list of steps used by TradeX-Setup.exe, install.ps1, install.sh and AI agents.

  python setup/tradex_setup.py --list                 steps and whether each is done
  python setup/tradex_setup.py --profile              what this machine is (OS, chip, RAM, drives, MT5 found ...)
  python setup/tradex_setup.py --all [--no-mt5] [--yes]   run every step that is not done yet (safe to re-run = repair)
  python setup/tradex_setup.py --run mt5_install      run one step
  add --json for one JSON line per result: {"step", "ok", "detail", "fix"}

Plain Python standard library only. Everything is kept inside the TradeX folder (the folder this repo is in):
private Python, packages, caches, temp files, MetaTrader 5 (portable) and your data (data/).
"""
import argparse, json, os, platform, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui  # console look (standard library only)

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the TradeX folder
DATA = os.path.join(APP, "data")
RUNTIME = os.path.join(APP, "runtime")
VENV = os.path.join(APP, ".venv")
MT5_DIR = os.path.join(APP, "mt5")
DOWNLOADS = os.path.join(RUNTIME, "downloads")
IS_WIN = os.name == "nt"
VPY = os.path.join(VENV, "Scripts" if IS_WIN else "bin", "python.exe" if IS_WIN else "python")
MT5_URL = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
OPTS = {"mt5": True, "yes": False, "wait_login": 600, "mt5_mode": None}   # mt5_mode: demo | own | skip

def env_inside():
    """Every cache and temp folder of every child process stays inside the TradeX folder (users' C: is often full)."""
    e = dict(os.environ)
    for k, sub in (("UV_CACHE_DIR", "uv-cache"), ("UV_PYTHON_INSTALL_DIR", "python"), ("UV_TOOL_DIR", "uv-tools"),
                   ("PIP_CACHE_DIR", "pip-cache"), ("TEMP", "tmp"), ("TMP", "tmp"), ("TMPDIR", "tmp")):
        e[k] = os.path.join(RUNTIME, sub)
        os.makedirs(e[k], exist_ok=True)
    e["TRADEX_DATA"] = DATA
    return e

def run(cmd, timeout=900, **kw):
    env = kw.pop("env", None) or env_inside()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, **kw)
    return r.returncode, (r.stdout + r.stderr).strip()

def uv():
    for p in (os.path.join(RUNTIME, "uv", "uv.exe" if IS_WIN else "uv"), shutil.which("uv")):
        if p and os.path.exists(p):
            return p
    return None

def read_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default

def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), indent=1)

# ------------------------------------------------------------------ step 0: machine profile
def _ram_gb():
    try:
        if IS_WIN:
            import ctypes
            class M(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong)] + \
                           [(f"x{i}", ctypes.c_ulonglong) for i in range(6)]
            m = M(); m.l = ctypes.sizeof(M)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.total / 2**30, 1)
        if sys.platform == "darwin":
            return round(int(subprocess.check_output(["sysctl", "-n", "hw.memsize"])) / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except Exception:
        return None

def _drives():
    roots = [f"{c}:\\" for c in "CDEFGHIJ" if os.path.exists(f"{c}:\\")] if IS_WIN else [os.path.expanduser("~")]
    out = {}
    for r in roots:
        try:
            out[r] = round(shutil.disk_usage(r).free / 2**30, 1)
        except OSError:
            pass
    return out

def _existing_mt5():
    found = []
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
            if base and os.path.isdir(base):
                for d in os.listdir(base):
                    p = os.path.join(base, d, "terminal64.exe")
                    if os.path.exists(p):
                        found.append(p)
    elif sys.platform == "darwin" and os.path.exists("/Applications/MetaTrader 5.app"):
        found.append("/Applications/MetaTrader 5.app")
    return found

def machine_profile():
    machine = platform.machine().lower()
    os_name = "windows" if IS_WIN else "mac" if sys.platform == "darwin" else "linux"
    arch = "arm64" if machine in ("arm64", "aarch64") or os.environ.get("PROCESSOR_ARCHITEW6432", "").lower() == "arm64" else "x64"
    ram = _ram_gb()
    lang = (os.environ.get("LANG") or "")[:2]
    if IS_WIN:
        try:
            import ctypes, locale
            lang = (locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage()) or "en")[:2]
        except Exception:
            pass
    p = {"os": os_name, "os_version": platform.version(), "arch": arch, "ram_gb": ram, "free_gb": _drives(),
         "app_folder": APP, "app_drive_free_gb": round(shutil.disk_usage(APP).free / 2**30, 1),
         "existing_mt5": _existing_mt5(), "language": lang if lang in ("bn", "hi") else "en",
         "light_mode": bool(ram and ram < 8),
         "mt5_route": "local" if os_name == "windows" else "none-yet (Mac/Linux: free sources; MT5 feed is R&D)"}
    return p

def check_profile():
    return os.path.exists(os.path.join(DATA, "machine.json")), "machine profile saved"

def do_profile():
    p = machine_profile()
    write_json(os.path.join(DATA, "machine.json"), p)
    return True, f"{p['os']} {p['arch']}, {p['ram_gb']} GB RAM, {p['app_drive_free_gb']} GB free here" + \
        (", Light mode on" if p["light_mode"] else "")

# ------------------------------------------------------------------ step 1: folder
def check_folder():
    ok = all(os.path.isdir(d) for d in (DATA, RUNTIME, DOWNLOADS)) and os.path.exists(os.path.join(APP, "hub", "server.py"))
    return ok, APP

def do_folder():
    if not os.path.exists(os.path.join(APP, "hub", "server.py")):
        return False, "TradeX app files are missing (hub/server.py)", "download TradeX again"
    free = shutil.disk_usage(APP).free / 2**30
    if free < 1:
        return False, f"only {free:.1f} GB free on this drive", "free 1 GB or pick another drive"
    for d in (DATA, RUNTIME, DOWNLOADS):
        os.makedirs(d, exist_ok=True)
    return True, f"{APP} ({free:.0f} GB free)"

# ------------------------------------------------------------------ step 2: packages (private Python via uv)
def _pkgs():
    extra = ["truststore"]
    if OPTS["mt5"] and IS_WIN:
        extra.append("MetaTrader5")
    return extra

def check_packages():
    if not os.path.exists(VPY):
        return False, "no private Python yet"
    code, out = run([VPY, "-c", "import websockets, numpy, pandas, pyarrow, truststore" +
                     (", MetaTrader5" if "MetaTrader5" in _pkgs() else "")], timeout=120)
    return code == 0, "packages ready" if code == 0 else out[-200:]

def do_packages():
    u = uv()
    if not u:
        return False, "uv not found", "run install.ps1 / install.sh (they fetch uv), or install uv"
    py = "3.12"
    # MetaTrader5 has wheels for x64 Windows only: on Windows on ARM ask uv for x64 Python (runs emulated).
    if IS_WIN and machine_profile()["arch"] == "arm64":
        py = "cpython-3.12-windows-x86_64-none"
    # Never rebuild an existing environment: a running TradeX holds python.exe open (Access denied on repair).
    if not os.path.exists(VPY):
        code, out = run([u, "venv", VENV, "--python", py], timeout=900)
        if code:
            return False, out[-300:], "check the internet connection and run setup again"
    code, out = run([u, "pip", "install", "--python", VPY, "-r", os.path.join(APP, "requirements.txt")] + _pkgs(), timeout=1800)
    if code:
        locked = "Access is denied" in out or "os error 5" in out or "os error 32" in out
        return False, out[-300:], ("close TradeX (its window) and run setup again" if locked
                                   else "check the internet connection and run setup again")
    return check_packages()

# ------------------------------------------------------------------ step 3: chart sources
def check_sources():
    return os.path.exists(os.path.join(DATA, "sources.json")), "sources chosen"

def do_sources():
    """Switch on only the no-key sources that answer from this network (e.g. Binance is blocked in the US)."""
    code, out = run([VPY if os.path.exists(VPY) else sys.executable, "-c",
                     "import json, feeds; print(json.dumps(feeds.probe()))"],
                    timeout=180, cwd=os.path.join(APP, "hub"))
    if code:
        return False, out[-300:], "run setup again; sources can also be switched on later in TradeX"
    res = json.loads(out.splitlines()[-1])
    write_json(os.path.join(DATA, "sources.json"), {k: v["on"] for k, v in res.items()})
    on = [k for k, v in res.items() if v["on"]]
    off = [f"{k} ({v['msg']})" for k, v in res.items() if not v["on"]]
    return True, f"on: {', '.join(on) or 'none'}" + (f"; off: {', '.join(off)}" if off else "")

# ------------------------------------------------------------------ steps 4-6: MetaTrader 5 (Windows)
MT5_SETUP = os.path.join(DOWNLOADS, "mt5setup.exe")
MT5_EXE = os.path.join(MT5_DIR, "terminal64.exe")

def _mt5_skip():
    if not OPTS["mt5"]:
        return "skipped (your choice; run setup again to add it)"
    if not IS_WIN:
        return "MT5 charts on Mac/Linux are not supported yet; free sources work"
    return None

def _own_mt5_source():
    """An MT5 already installed on this PC (its program folder), or None. Seen 2026-11-24: when one exists,
    MetaQuotes' mt5setup.exe ignores /path and UPDATES that installation (and restarts it) instead of making ours.
    So TradeX then copies only the program files from it and never runs mt5setup."""
    for p in (_existing_mt5() if IS_WIN else []):
        d = os.path.dirname(p)
        if os.path.normcase(os.path.abspath(d)) != os.path.normcase(os.path.abspath(MT5_DIR)):
            return d
    return None

def check_mt5_download():
    if _mt5_skip():
        return True, _mt5_skip()
    if os.path.exists(MT5_EXE):
        return True, "MetaTrader 5 already in the TradeX folder"
    src = _own_mt5_source()
    if src:
        return True, f"not needed: your MetaTrader 5 is on this PC ({src}); TradeX copies its program files"
    return os.path.exists(MT5_SETUP) and os.path.getsize(MT5_SETUP) > 10e6, "installer downloaded"

def do_mt5_download():
    # Windows curl.exe uses the Windows certificate store; Python's own HTTPS failed on this host (2026-11-24).
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        return False, "curl.exe not found (Windows 10 1803 or newer has it)", "update Windows"
    os.makedirs(DOWNLOADS, exist_ok=True)
    code, out = run([curl, "-L", "--fail", "--retry", "3", "-o", MT5_SETUP, MT5_URL], timeout=900)
    if code or not os.path.exists(MT5_SETUP):
        return False, f"download failed: {out[-200:]}", "check the internet connection and run setup again"
    code, sig = run(["powershell", "-NoProfile", "-Command",
                     f"(Get-AuthenticodeSignature '{MT5_SETUP}') | % {{ $_.Status.ToString() + '|' + $_.SignerCertificate.Subject }}"], timeout=60)
    if "Valid" not in sig or "MetaQuotes" not in sig:
        os.remove(MT5_SETUP)
        return False, f"installer signature not MetaQuotes ({sig[:120]}); deleted", "run setup again"
    return True, f"{os.path.getsize(MT5_SETUP) / 1e6:.1f} MB, signed by MetaQuotes"

def check_mt5_install():
    if _mt5_skip():
        return True, _mt5_skip()
    if os.path.exists(MT5_EXE) and os.path.exists(BEFORE_FILE) and not _terminal_running():
        _remove_first_start_leftovers(read_json(BEFORE_FILE, []))  # a first-start folder that appeared late
    return os.path.exists(MT5_EXE), MT5_EXE

def do_mt5_install():
    src = _own_mt5_source()
    if src:
        # The person's own MT5 stays untouched: copy only program files (no accounts, settings, robots, charts).
        os.makedirs(os.path.join(MT5_DIR, "Config"), exist_ok=True)
        n = 0
        for f in os.listdir(src):
            p = os.path.join(src, f)
            if os.path.isfile(p) and f.lower().endswith((".exe", ".dll", ".ico")) and f.lower() != "uninstall.exe":
                shutil.copy2(p, os.path.join(MT5_DIR, f)); n += 1
        if os.path.isdir(os.path.join(src, "Sounds")):
            shutil.copytree(os.path.join(src, "Sounds"), os.path.join(MT5_DIR, "Sounds"), dirs_exist_ok=True)
        if os.path.exists(os.path.join(src, "Config", "servers.dat")):
            shutil.copy2(os.path.join(src, "Config", "servers.dat"), os.path.join(MT5_DIR, "Config", "servers.dat"))
        if not os.path.exists(MT5_EXE):
            return False, f"could not copy MetaTrader 5 from {src}", "run setup again"
        return True, f"copied {n} program files from your MetaTrader 5 ({src}); yours is not changed"
    # No MT5 on this PC: official silent install into the TradeX folder. /auto can return before the files are there.
    if not os.path.exists(MT5_SETUP):
        return False, "MT5 installer not downloaded yet", "run the mt5_download step first (or run setup again)"
    before = sorted(_appdata_terminals())
    write_json(BEFORE_FILE, before)  # kept so a later check can still clean up a folder that appears late
    proc = subprocess.Popen([MT5_SETUP, "/auto", f"/path:{MT5_DIR}"], env=env_inside())
    t = time.time()
    try:
        proc.wait(timeout=600)  # the installer exits after its one normal-mode start of the terminal
    except subprocess.TimeoutExpired:
        pass
    while time.time() - t < 600 and not os.path.exists(MT5_EXE):
        time.sleep(3)
    if not os.path.exists(MT5_EXE):
        return False, "MT5 files did not appear within 10 minutes", "run setup again"
    _wait_terminal_closed(60)
    removed = _remove_first_start_leftovers(before)
    return True, f"installed in {MT5_DIR} ({time.time() - t:.0f} s)" + \
        (f"; removed {len(removed)} first-start folder(s) from AppData" if removed else "")

BEFORE_FILE = os.path.join(DATA, "mt5_appdata_before.json")

def _terminal_running():
    """True when the TradeX copy of MT5 (this exact path) is running. Never looks at other MT5 installs."""
    if not IS_WIN:
        return False
    code, out = run(["powershell", "-NoProfile", "-Command",
                     f"@(Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq '{MT5_EXE}' }}).Count"],
                    timeout=60)
    return out.strip().splitlines()[-1:] != ["0"] if code == 0 else False

def _wait_terminal_closed(seconds):
    t = time.time()
    while time.time() - t < seconds and _terminal_running():
        time.sleep(3)

def _appdata_terminals():
    root = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal")
    if not os.path.isdir(root):
        return []
    return [d for d in os.listdir(root) if len(d) == 32 and os.path.isdir(os.path.join(root, d))]

def _remove_first_start_leftovers(before, dry_run=False):
    """mt5setup.exe /auto starts the new terminal once in normal (non-portable) mode, which writes a small data folder
    to %APPDATA%\\MetaQuotes\\Terminal\\<hash> on C: (seen 2026-11-24: config + logs, 0.75 MB). TradeX always starts
    MT5 with /portable, so that folder is never used again. Remove ONLY a folder that is new since the install began,
    holds nothing but config/logs and is under 5 MB, so another MT5 the person uses can never be touched."""
    root = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal")
    removed = []
    for d in set(_appdata_terminals()) - set(before):
        p = os.path.join(root, d)
        names = set(os.listdir(p))
        size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(p) for f in fs)
        if names <= {"config", "logs"} and size < 5e6:
            if not dry_run:
                shutil.rmtree(p, ignore_errors=True)
            removed.append(d)
    return removed

def _mt5_logged_in():
    code, out = run([VPY, "-c", "import MetaTrader5 as m, json\n"
                     f"ok=m.initialize(path=r'{MT5_EXE}', portable=True, timeout=15000)\n"
                     "a=m.account_info() if ok else None\n"
                     "print(json.dumps({'ok':bool(a),'server':a.server if a else None,'demo':a.trade_mode==0 if a else None,'err':str(m.last_error())}))\n"
                     "m.shutdown()"], timeout=90)
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"ok": False, "err": out[-200:]}

def check_mt5_login():
    if _mt5_skip():
        return True, _mt5_skip()
    if not (os.path.exists(MT5_EXE) and os.path.exists(VPY)):
        return False, "MT5 or packages missing"
    if not _terminal_running():
        # Checking must never pop MT5 onto the screen (agents call --list), so only a running terminal is asked.
        return False, "MT5 is not running, so the login was not checked (TradeX starts it when TradeX starts)"
    r = _mt5_logged_in()
    return r["ok"], f"logged in to {r.get('server')}" + (" (demo)" if r.get("demo") else "") if r["ok"] else r.get("err", "")

MT5_CHOICE = os.path.join(DATA, "mt5_choice.json")

def ask_mt5_mode():
    """The one MT5 question (asked once, saved): 1 = your own MT5 account, 2 = create a free demo for me, 3 = skip.
    Enter, --yes or no keyboard (agents) = 2. --mt5-mode demo|own|skip answers it without asking."""
    if not IS_WIN or not OPTS["mt5"]:
        OPTS["mt5_mode"] = "skip"; return
    if OPTS["mt5_mode"]:
        write_json(MT5_CHOICE, {"mode": OPTS["mt5_mode"]}); return
    saved = read_json(MT5_CHOICE, {}).get("mode")
    if saved:
        OPTS["mt5_mode"] = saved
    elif OPTS["yes"] or AS_JSON or not sys.stdin.isatty():
        OPTS["mt5_mode"] = "demo"
    else:
        OPTS["mt5_mode"] = ui.menu("MetaTrader 5", "Forex, gold, index and US stock charts. Used only to READ charts.",
                                   [("own", "I have an MT5 account (login number, password, server)"),
                                    ("demo", "Create a free demo account for me (nothing to type)"),
                                    ("skip", "Skip MetaTrader 5 (free crypto and forex exchanges only)")], "demo")
    if OPTS["mt5_mode"] == "skip":
        OPTS["mt5"] = False
    write_json(MT5_CHOICE, {"mode": OPTS["mt5_mode"]})

def _login_own():
    """Ask login, server and password (hidden) and log in once through the MetaTrader5 package. MT5 itself keeps the
    account; TradeX never writes the password anywhere (it goes to the child process in an environment variable)."""
    import getpass
    if not sys.stdin.isatty():   # the hidden password needs a person at the keyboard (agents: use demo mode)
        return {"ok": False, "err": "your own account needs a person at the keyboard; run setup in a terminal, or use --mt5-mode demo"}
    ui.box(["MT5 needs 3 things: see your broker's welcome email",
            "or MT5 itself (File > Login to Trade Account).",
            "",
            "1  Login number   digits only, e.g. 12345678",
            "2  Server         exact name, e.g. MetaQuotes-Demo",
            "3  Password       best: the INVESTOR (read-only) one.",
            "                  TradeX only reads charts; with it",
            "                  nobody can trade on your account.",
            "",
            "The password stays hidden and TradeX never saves it."], title="Your MT5 account")
    login = input("  Login number: ").strip()
    if not login.isdigit():
        return {"ok": False, "err": "the login must be a number"}
    server = input("  Server: ").strip()
    if not server:
        return {"ok": False, "err": "the server name is needed (the same number can exist at different brokers)"}
    pw = getpass.getpass("  Password (hidden): ")
    env = dict(env_inside(), TX_MT5_PW=pw)
    code, out = run([VPY, "-c", "import MetaTrader5 as m, os, json\n"
                     f"ok=m.initialize(path=r'{MT5_EXE}', portable=True, login={int(login)}, password=os.environ['TX_MT5_PW'], server={server!r}, timeout=30000)\n"
                     "a=m.account_info() if ok else None\n"
                     "print(json.dumps({'ok':bool(a),'server':a.server if a else None,'err':str(m.last_error())}))\n"
                     "m.shutdown()"], timeout=120, env=env)
    del env, pw
    try:
        r = json.loads(out.splitlines()[-1])
    except Exception:
        r = {"ok": False, "err": out[-200:]}
    return r

def do_mt5_login():
    """Mode demo: setup/mt5_demo.ps1 fills MT5's own 'Open an Account' window with placeholder details (fallback: the
    person fills it). Mode own: login + server + hidden password. Then setup waits until the terminal is logged in."""
    si = None
    if IS_WIN:   # start MetaTrader 5 minimized and without taking focus: nothing pops up for the person
        si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    proc = subprocess.Popen([MT5_EXE, "/portable"], env=env_inside(), startupinfo=si)
    mode = OPTS["mt5_mode"] or "demo"
    if mode == "own":
        say("MetaTrader 5 is opening. Close its 'Open an Account' window if it shows; TradeX logs in for you.")
        time.sleep(15)
        r = _login_own()
        if r["ok"]:
            return True, f"logged in to {r['server']}"
        say(f"That login did not work ({r.get('err')}). In the MetaTrader 5 window: File > Login to Trade Account, "
            "type the login, password and your broker's server (typing the broker name finds its servers).")
    else:
        say("MetaTrader 5 is opening. TradeX is creating a free demo account in it (about 1 minute, do not click it).")
        code, out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                         os.path.join(APP, "setup", "mt5_demo.ps1"), "-ProcessId", str(proc.pid)], timeout=240)
        try:
            r = json.loads(out.strip().splitlines()[-1])
        except Exception:
            r = {"ok": False, "step": "?", "msg": out[-200:]}
        if r.get("ok"):
            for _ in range(12):
                time.sleep(5)
                lr = _mt5_logged_in()
                if lr["ok"]:
                    return True, f"demo account opened and logged in to {lr['server']}"
        say(f"Automatic demo did not finish ({r.get('step')}: {r.get('msg')}). Please finish it in the MetaTrader 5 "
            "window: choose 'MetaQuotes Ltd.', 'Open a demo account', fill in the form, Next, Finish.")
    t = time.time()
    while time.time() - t < OPTS["wait_login"]:
        time.sleep(10)
        r = _mt5_logged_in()
        if r["ok"]:
            return True, f"logged in to {r['server']}"
    return False, "no MT5 account yet", "open MetaTrader 5, log in (or open a demo account), then run setup again"

# ------------------------------------------------------------------ step 7: launcher + settings
LAUNCH = os.path.join(APP, "TradeX.cmd" if IS_WIN else "tradex.sh")

def _mt5_wanted():
    return os.path.exists(MT5_EXE) and OPTS["mt5"] and IS_WIN

def check_launcher():
    env_path = os.path.join(APP, ".env")
    if not (os.path.exists(LAUNCH) and os.path.exists(env_path)):
        return False, "launcher not made yet"
    env = dict(l.strip().split("=", 1) for l in open(env_path, encoding="utf-8") if "=" in l and not l.startswith("#"))
    if env.get("LOCAL_MT5") != ("1" if _mt5_wanted() else "0"):
        return False, ".env does not match the installed MT5"
    return True, LAUNCH

def do_launcher():
    env_path = os.path.join(APP, ".env")
    keep = [l for l in open(env_path, encoding="utf-8")] if os.path.exists(env_path) else []
    keep = [l for l in keep if not l.split("=")[0].strip() in ("LOCAL_MT5", "MT5_TERMINAL_PATH", "MT5_PORTABLE", "TRADEX_DATA")]
    mt5_on = _mt5_wanted()
    keep += ["# written by setup/tradex_setup.py\n", f"LOCAL_MT5={1 if mt5_on else 0}\n", f"TRADEX_DATA={DATA}\n"]
    if mt5_on:
        keep += [f"MT5_TERMINAL_PATH={MT5_EXE}\n", "MT5_PORTABLE=1\n"]
    # Ports: a value set for this install (environment) is saved, so the shortcut and the server always agree
    saved = {l.split("=", 1)[0].strip(): l.split("=", 1)[1].strip() for l in keep if "=" in l and not l.startswith("#")}
    for k in ("HUB_HTTP_PORT", "HUB_WS_PORT"):
        if os.environ.get(k) and saved.get(k) != os.environ[k]:
            keep = [l for l in keep if l.split("=")[0].strip() != k] + [f"{k}={os.environ[k]}\n"]
    open(env_path, "w", encoding="utf-8").writelines(keep)
    port = saved.get("HUB_HTTP_PORT") or "8770"
    port = os.environ.get("HUB_HTTP_PORT", port)
    if IS_WIN and os.environ.get("TRADEX_NO_SHORTCUT") == "1":   # nothing outside the TradeX folder (no Start-menu entry)
        open(LAUNCH, "w").write(
            "@echo off\r\ncd /d \"%~dp0\"\r\n"
            f"start \"\" http://localhost:{port}/hub.html\r\n"
            "\".venv\\Scripts\\python.exe\" hub\\server.py\r\n")
        return True, f"start TradeX with {LAUNCH}"
    if IS_WIN:
        open(LAUNCH, "w").write(
            "@echo off\r\ncd /d \"%~dp0\"\r\n"
            f"start \"\" http://localhost:{port}/hub.html\r\n"
            "\".venv\\Scripts\\python.exe\" hub\\server.py\r\n")
        lnk = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "TradeX.lnk")
        run(["powershell", "-NoProfile", "-Command",
             f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{LAUNCH}';"
             f"$s.WorkingDirectory='{APP}';$s.Save()"], timeout=60)
    else:
        open(LAUNCH, "w").write(f"#!/bin/sh\ncd \"$(dirname \"$0\")\"\n(sleep 3; open http://localhost:{port}/hub.html 2>/dev/null || "
                                f"xdg-open http://localhost:{port}/hub.html) &\nexec .venv/bin/python hub/server.py\n")
        os.chmod(LAUNCH, 0o755)
    return True, f"start TradeX with {LAUNCH}" + (" (Start menu: TradeX)" if IS_WIN else "")

STEPS = [("profile", check_profile, do_profile), ("folder", check_folder, do_folder),
         ("packages", check_packages, do_packages), ("sources", check_sources, do_sources),
         ("mt5_download", check_mt5_download, do_mt5_download), ("mt5_install", check_mt5_install, do_mt5_install),
         ("mt5_login", check_mt5_login, do_mt5_login), ("launcher", check_launcher, do_launcher)]

# ------------------------------------------------------------------ CLI
AS_JSON = False

def say(msg):
    print(json.dumps({"say": msg}), flush=True) if AS_JSON else ui.note(msg)

LABELS = {"profile": "Checking this computer", "folder": "Preparing the TradeX folder", "packages": "Installing Python and packages",
          "sources": "Testing the free chart sources", "mt5_download": "Downloading MetaTrader 5", "mt5_install": "Installing MetaTrader 5",
          "mt5_login": "MetaTrader 5 account", "launcher": "Making the TradeX shortcut"}

def report(step, ok, detail, fix=""):
    if AS_JSON:
        print(json.dumps({"step": step, "ok": ok, "detail": detail, "fix": fix}), flush=True)
    else:
        ui.line("ok" if ok else ("wait" if step == "mt5_login" else "err"), LABELS.get(step, step), detail)
        if fix and not ok:
            print(f"    {ui.DIM}fix: {fix}{ui.RESET}", flush=True)

def do_step(name, chk, act, force=False):
    try:
        if not force:
            ok, detail = chk()
            if ok:
                return report(name, True, detail) or True
        if AS_JSON or name == "mt5_login":   # the login step asks questions: no spinner over the prompts
            r = act()
            report(name, r[0], r[1], r[2] if len(r) > 2 else "")
            return r[0]
        with ui.Step(LABELS.get(name, name)) as st:
            r = act()
            st.done(r[1]) if r[0] else st.fail(r[1], r[2] if len(r) > 2 else "")
        return r[0]
    except Exception as e:
        report(name, False, f"{e.__class__.__name__}: {e}", "run setup again; if it repeats, open an issue with this line")
        return False

def main():
    global AS_JSON
    a = argparse.ArgumentParser(description="TradeX setup steps")
    a.add_argument("--list", action="store_true"); a.add_argument("--profile", action="store_true")
    a.add_argument("--all", action="store_true"); a.add_argument("--run")
    a.add_argument("--force", action="store_true", help="run the step even if its check passes")
    a.add_argument("--no-mt5", action="store_true"); a.add_argument("--yes", action="store_true")
    a.add_argument("--login-wait", type=int, default=600, help="seconds to wait for the MT5 demo form")
    a.add_argument("--mt5-mode", choices=["demo", "own", "skip"], help="answer the MT5 question: create a demo, use own account, or skip")
    a.add_argument("--json", action="store_true")
    o = a.parse_args()
    OPTS["mt5_mode"] = o.mt5_mode
    AS_JSON, OPTS["mt5"], OPTS["yes"], OPTS["wait_login"] = o.json, not o.no_mt5, o.yes, o.login_wait
    if o.profile:
        print(json.dumps(machine_profile(), indent=None if o.json else 1)); return 0
    if o.list:
        for name, chk, _ in STEPS:
            try:
                ok, d = chk()
            except Exception as e:
                ok, d = False, str(e)
            report(name, ok, d)
        return 0
    if o.run:
        if o.run.startswith("mt5"):
            ask_mt5_mode()
        step = next((s for s in STEPS if s[0] == o.run), None)
        if not step:
            print("unknown step; see --list"); return 2
        return 0 if do_step(*step, force=o.force) else 1
    if o.all:
        os.makedirs(DATA, exist_ok=True)
        if not AS_JSON:
            ui.banner("v1.0", APP, "Python, packages, chart sources" + (", MetaTrader 5" if OPTS["mt5"] and IS_WIN else ""))
        ask_mt5_mode()
        pending = False
        for s in STEPS:
            if not do_step(*s, force=o.force):
                if s[0] != "mt5_login":
                    return 1
                # The demo form needs a person and may take longer: TradeX still gets set up and works with the
                # free sources; the MT5 feed turns green by itself once they have logged in.
                pending = True
        if pending:
            say("TradeX is ready with the free chart sources. MetaTrader 5 is installed and waits for you to open a "
                "demo account (or log in) in its window; its charts appear in TradeX after that.")
            return 3
        say("TradeX is ready. Start it with " + LAUNCH + ("" if os.environ.get("TRADEX_NO_SHORTCUT") == "1" or not IS_WIN else " or from the Start menu (TradeX)"))
        return 0
    a.print_help(); return 0

if __name__ == "__main__":
    sys.exit(main())
