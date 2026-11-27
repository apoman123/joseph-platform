"""TradeX installer console look (standard library only): amber block-letter banner + info box, one line per step with
a spinner that turns into a check, boxed numbered menus. Plain text when output is not a terminal (logs, agents)."""
import os, sys, threading, time, itertools

TTY = sys.stdout.isatty()
if os.name == "nt":
    os.system("")  # turns on colour (ANSI) handling in the classic PowerShell / cmd window
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
MODERN = os.name != "nt" or bool(os.environ.get("WT_SESSION")) or os.environ.get("TERM_PROGRAM") == "vscode"
TL, TR, BL, BR = ("╭", "╮", "╰", "╯") if MODERN else ("┌", "┐", "└", "┘")
H, V = "─", "│"
CHECK, CROSS, WAIT = ("✓", "✗", "○") if MODERN else ("√", "x", "o")
SPIN = "⣻⢿⡿⣟⣯⣷⣾⣽" if MODERN else "|/-\\"

def rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m" if TTY else ""
RESET = "\033[0m" if TTY else ""
BOLD = "\033[1m" if TTY else ""
DIM = rgb(140, 140, 140)
AMBER = rgb(245, 166, 35)
GOLD = rgb(255, 214, 102)
GREEN = rgb(80, 200, 120)
RED = rgb(235, 87, 87)

LETTERS = {  # 5 rows each; the banner reads "TradeX" (capital T and X, lowercase rade)
    "T": ["██████", "  ██  ", "  ██  ", "  ██  ", "  ██  "],
    "r": ["     ", "█ ███", "██   ", "██   ", "██   "],
    "a": ["      ", " ████ ", "  ▄▄██", "██  ██", " ████▀"],
    "d": ["    ██", " ▄▄▄██", "██  ██", "██  ██", " ████▀"],
    "e": ["      ", " ████ ", "██▄▄██", "██    ", " ▀███ "],
    "X": ["██  ██", " ████ ", "  ██  ", " ████ ", "██  ██"],
}

def _grad(text, a=(245, 130, 30), b=(255, 214, 102)):
    if not TTY:
        return text
    n = max(1, len(text) - 1)
    return "".join(rgb(*[int(a[k] + (b[k] - a[k]) * i / n) for k in range(3)]) + ch for i, ch in enumerate(text)) + RESET

def box(lines, title="", width=None, color=AMBER):
    """Rounded (or square, in the classic window) box around text lines."""
    w = width or max([len(_plain(l)) for l in lines] + [len(title) + 4]) + 2
    top = TL + H + (f" {title} " if title else "") + H * (w - len(title) - (3 if title else 1)) + TR
    print(color + top + RESET)
    for l in lines:
        print(color + V + RESET + " " + l + " " * (w - 1 - len(_plain(l))) + color + V + RESET)
    print(color + BL + H * w + BR + RESET)

def _plain(s):
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)

def banner(version, folder, steps):
    print()
    for row in range(5):
        print("  " + _grad(" ".join(LETTERS[c][row] for c in "TradeX")))
    print("  " + DIM + "the open-source TradingView" + RESET + "\n")
    box([f"{BOLD}TradeX Installer{RESET}  {DIM}{version}{RESET}",
         f"{DIM}Folder{RESET}  {folder}",
         f"{DIM}Steps {RESET}  {steps}",
         f"{DIM}No admin rights needed. Safe to run again (= update).{RESET}"])
    print()

class Step:
    """with Step('Installing packages') as s: ... ; s.done('3 packages') / s.fail('why'). Spinner while it runs."""
    def __init__(self, label):
        self.label, self.result, self._stop = label, None, threading.Event()
    def __enter__(self):
        if TTY:
            self._t = threading.Thread(target=self._spin, daemon=True); self._t.start()
        return self
    def _spin(self):
        for ch in itertools.cycle(SPIN):
            if self._stop.is_set():
                return
            sys.stdout.write(f"\r  {AMBER}{ch}{RESET} {self.label}"); sys.stdout.flush(); time.sleep(0.09)
    def done(self, detail=""):
        self.result = (True, detail)
    def fail(self, detail="", fix=""):
        self.result = (False, detail, fix)
    def __exit__(self, *exc):
        self._stop.set()
        if TTY:
            self._t.join(); sys.stdout.write("\r\033[2K")
        ok, detail = (self.result or (False, "stopped"))[:2]
        fix = self.result[2] if self.result and len(self.result) > 2 else ""
        mark = f"{GREEN}{CHECK}{RESET}" if ok else f"{RED}{CROSS}{RESET}"
        print(f"  {mark} {self.label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
        if fix and not ok:
            print(f"    {DIM}fix: {fix}{RESET}")
        return False

def line(kind, label, detail=""):
    mark = {"ok": f"{GREEN}{CHECK}{RESET}", "err": f"{RED}{CROSS}{RESET}", "wait": f"{GOLD}{WAIT}{RESET}"}.get(kind, " ")
    print(f"  {mark} {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""), flush=True)

def note(msg):
    print(f"  {AMBER}>{RESET} {msg}", flush=True)

def menu(title, intro, options, default):
    """Boxed numbered menu. options: list of (key, text). Returns the chosen key; Enter = default."""
    rows = [f"{DIM}{intro}{RESET}", ""]
    for i, (k, text) in enumerate(options, 1):
        tag = f"  {GOLD}(recommended){RESET}" if k == default else ""
        rows.append(f"{AMBER}{BOLD}{i}{RESET}  {text}{tag}")
    print(); box(rows, title=title)
    d = next(i for i, (k, _) in enumerate(options, 1) if k == default)
    a = input(f"  Choose 1-{len(options)} [Enter = {d}]: ").strip()
    return options[int(a) - 1][0] if a.isdigit() and 1 <= int(a) <= len(options) else default
