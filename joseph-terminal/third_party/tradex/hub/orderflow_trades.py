"""Every Binance futures trade for the footprint chart, loaded fast and kept (TradeX, 2026-11-15).

Past UTC days come from data.binance.vision daily files (downloaded in the background, cached as parquet).
Recent hours come from the REST aggTrades list: newest gaps first, one shared weight budget for the whole server,
and every complete hour is saved to disk so a restart does not start over.
Coverage is reported as exact time ranges, so the chart knows which minutes hold every trade.
"""
from __future__ import annotations

import bisect
import json
import os
import threading
import time
import urllib.error
import urllib.request
import datetime as _dt

import numpy as np

FAPI = "https://fapi.binance.com/fapi/v1"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "rest_trades")
MAX_PAST_DAYS = 2            # daily files used: at most this many past days (BTC ~30 MB each)
MAX_REST_HOURS = 30          # REST reaches back at most this far (covers yesterday until its daily file appears)
POLL_S = 4.0                 # newest trades polled this often per symbol in use
IDLE_STOP_S = 600            # a symbol nobody asked for in 10 minutes stops loading


# ---------------- one weight budget for every Binance call of this server ----------------
class _Weight:
    """Binance allows 2400 weight per minute per IP. This server uses at most BUDGET, and stops everything when
    Binance says 429 (slow down) or 418 (banned), for as long as Retry-After asks."""
    BUDGET = 1900
    RESERVE = 400

    def __init__(self):
        self.lock = threading.Lock()
        self.used = []           # (time, weight)
        self.until = 0.0         # no calls before this (after 429/418)
        self.server_used = 0     # last X-MBX-USED-WEIGHT-1M seen

    def take(self, w, low=False):
        """low: background loading. It leaves RESERVE free so a chart request never waits behind it."""
        cap = self.BUDGET - (self.RESERVE if low else 0)
        while True:
            with self.lock:
                now = time.time()
                self.used = [x for x in self.used if now - x[0] < 60]
                spent = sum(x[1] for x in self.used)
                wait = self.until - now
                if wait <= 0 and spent + w <= cap and self.server_used + w < (1800 if low else 2200):
                    self.used.append((now, w))
                    return
                if wait <= 0:
                    wait = 60 - (now - self.used[0][0]) if self.used else 1.0
                    if self.server_used + w >= (1800 if low else 2200):
                        self.server_used = 0     # re-read on the next reply
            time.sleep(min(max(wait, 0.2), 5))

    def get(self, url, w, timeout=20, low=False):
        self.take(w, low)
        req = urllib.request.Request(url, headers={"User-Agent": "tradex/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                u = r.headers.get("X-MBX-USED-WEIGHT-1M")
                if u and u.isdigit():
                    self.server_used = int(u)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                ra = e.headers.get("Retry-After")
                with self.lock:
                    self.until = time.time() + (int(ra) if ra and ra.isdigit() else 60)
            raise


WEIGHT = _Weight()


def _utc_ms(d):
    return int(_dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _arr(rows):
    """REST rows -> (ids, t_ms, price, qty, side) numpy arrays."""
    return (np.array([r["a"] for r in rows], "int64"), np.array([r["T"] for r in rows], "int64"),
            np.array([float(r["p"]) for r in rows]), np.array([float(r["q"]) for r in rows]),
            np.array([-1 if r["m"] else 1 for r in rows], "int8"))


class _Seg:
    """Trades with consecutive ids: chunks in id order, plus the exact time range they fully cover."""

    def __init__(self, chunk, t0, t1):
        self.chunks = [chunk]
        self.t0, self.t1 = t0, t1

    @property
    def first(self):
        return int(self.chunks[0][0][0])

    @property
    def last(self):
        return int(self.chunks[-1][0][-1])


class TradeStore:
    """All trades of one Binance futures symbol that the charts asked for."""

    def __init__(self, sym):
        self.sym, self.lock = sym, threading.Lock()
        self.segs = []                      # _Seg list, sorted by id
        self.floor_ms = None                # REST fills back to here
        self.days, self.day_busy, self.day_fail = {}, set(), {}
        self.thread, self.last_used, self.last_poll, self.saved = None, time.time(), 0.0, set()
        self._load_hours()

    # ----- disk: one file per complete UTC hour -----
    def _dir(self):
        return os.path.join(CACHE, self.sym)

    def _load_hours(self):
        try:
            names = sorted(os.listdir(self._dir()))
        except OSError:
            return
        oldest = time.time() * 1000 - MAX_REST_HOURS * 3600_000
        for n in names:
            if not n.endswith(".npz"):
                continue
            h = int(_dt.datetime.strptime(n[:10], "%Y%m%d%H").replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)
            path = os.path.join(self._dir(), n)
            if h < oldest - 3 * 3600_000:
                try: os.remove(path)
                except OSError: pass
                continue
            try:
                z = np.load(path)
                chunk = (z["id"], z["t"], z["p"], z["q"], z["s"])
            except Exception:
                continue
            if len(chunk[0]):
                self._add(chunk, h, h + 3600_000 - 1)
                self.saved.add(h)

    def _save_hours(self):
        os.makedirs(self._dir(), exist_ok=True)
        with self.lock:
            segs = [(s.t0, s.t1, list(s.chunks)) for s in self.segs]
        for t0, t1, chunks in segs:
            h = -(-t0 // 3600_000) * 3600_000          # first whole hour inside
            while h + 3600_000 - 1 <= t1:
                if h not in self.saved:
                    parts = [c for c in chunks if c[1][-1] >= h and c[1][0] < h + 3600_000]
                    if parts:
                        cols = [np.concatenate([c[i] for c in parts]) for i in range(5)]
                        k = (cols[1] >= h) & (cols[1] < h + 3600_000)
                        if k.any():
                            name = _dt.datetime.fromtimestamp(h / 1000, _dt.timezone.utc).strftime("%Y%m%d%H")
                            tmp = os.path.join(self._dir(), name + ".part.npz")
                            np.savez(tmp, id=cols[0][k], t=cols[1][k], p=cols[2][k], q=cols[3][k], s=cols[4][k])
                            os.replace(tmp, os.path.join(self._dir(), name + ".npz"))
                    self.saved.add(h)
                h += 3600_000

    # ----- memory: segments of consecutive ids -----
    def _add(self, chunk, t0, t1):
        """Insert trades with consecutive ids covering [t0, t1]; merge with neighbours whose ids touch."""
        with self.lock:
            self.segs.append(_Seg(chunk, t0, t1))
            self.segs.sort(key=lambda s: s.first)
            out = [self.segs[0]]
            for s in self.segs[1:]:
                cur = out[-1]
                if s.first <= cur.last + 1:          # touching or overlapping ids: nothing missing between them
                    for c in s.chunks:
                        k = c[0] > cur.last
                        if k.any():
                            cur.chunks.append(tuple(x[k] for x in c))
                    cur.t0, cur.t1 = min(cur.t0, s.t0), max(cur.t1, s.t1)
                else:
                    out.append(s)
            self.segs = out

    def _rest(self, low=True, **kw):
        q = "&".join(f"{k}={v}" for k, v in kw.items())
        rows = WEIGHT.get(f"{FAPI}/aggTrades?symbol={self.sym}&limit=1000" + ("&" + q if q else ""), 20, low=low)
        return _arr(rows) if rows else None

    def _poll_head(self):
        t_req = time.time() * 1000 - 1000
        with self.lock:
            head = self.segs[-1] if self.segs else None
        fresh = head is not None and t_req - head.t1 < 120_000
        # live updates go ahead of background loading, so the forming candle never drops back to "estimated"
        c = self._rest(low=False, fromId=head.last + 1) if fresh else self._rest(low=False)
        if c is None:
            if fresh:
                with self.lock:
                    head.t1 = max(head.t1, int(t_req))
            return False
        more = fresh and len(c[0]) == 1000
        self._add(c, int(c[1][0]) + 1 if not fresh else head.t1, int(c[1][-1]) if more else int(max(t_req, c[1][-1])))
        return more

    def _back_step(self):
        """Fill the newest missing stretch (between segments, else below the oldest), 1000 trades per call."""
        with self.lock:
            segs, floor = list(self.segs), self.floor_ms
        if not segs or floor is None:
            return False
        for i in range(len(segs) - 1, 0, -1):
            lo, hi = segs[i - 1], segs[i]
            if hi.t0 > floor:
                c = self._rest(fromId=max(lo.last + 1, hi.first - 1000))
                break
        else:
            if segs[0].t0 <= floor or segs[0].first == 0:
                return False
            c = self._rest(fromId=max(0, segs[0].first - 1000))
        if c is None:
            return False
        self._add(c, int(c[1][0]) + 1, int(c[1][-1]))
        return True

    def _run_head(self):
        """Own thread: newest trades every POLL_S, never stuck behind background loading."""
        while time.time() - self.last_used < IDLE_STOP_S:
            try:
                self.last_poll = time.time()
                while self._poll_head():
                    pass
            except Exception:
                time.sleep(2)
            time.sleep(max(0.2, POLL_S - (time.time() - self.last_poll)))

    def _run(self):
        last_save = time.time()
        threading.Thread(target=self._run_head, daemon=True).start()
        while time.time() - self.last_used < IDLE_STOP_S:
            try:
                if not self._back_step():
                    time.sleep(0.5)
                if time.time() - last_save > 60:
                    last_save = time.time()
                    self._save_hours()
                    self._evict()
            except Exception:
                time.sleep(5)
        try:
            self._save_hours()
        except Exception:
            pass
        self.thread = None

    def _evict(self):
        old = time.time() * 1000 - (MAX_REST_HOURS + 2) * 3600_000
        with self.lock:
            self.segs = [s for s in self.segs if s.t1 >= old]

    # ----- past days: daily files, downloaded in the background -----
    def _day_job(self, d):
        try:
            import orderflow as _of
            import pandas as pd
            df = pd.read_parquet(_of.fetch_day(d, "aggTrades", self.sym))
            ms = ((df["ts"] - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy().astype("int64")
            arrs = (ms, df["price"].to_numpy(), df["qty"].to_numpy(), df["side"].to_numpy().astype("int8"))
            with self.lock:
                self.days[d] = arrs
        except Exception:      # yesterday's file appears a few hours after midnight: REST covers it until then
            with self.lock:
                self.day_fail[d] = time.time()
        finally:
            with self.lock:
                self.day_busy.discard(d)

    def _day(self, d):
        """Arrays of day d, or 'busy' (downloading), or None (no file yet / failed)."""
        with self.lock:
            if d in self.days:
                return self.days[d]
            if d in self.day_busy:
                return "busy"
            if time.time() - self.day_fail.get(d, 0) < 1800:
                return None
            self.day_busy.add(d)
        threading.Thread(target=self._day_job, args=(d,), daemon=True).start()
        return "busy"

    # ----- what the charts call -----
    def trades(self, t0_ms, t1_ms):
        """Trades in [t0, t1] that are fully loaded, the time ranges they fully cover, and whether more is coming.
        Returns ((t_ms, price, qty, side), covered [(a, b)...], pending)."""
        now = time.time() * 1000
        self.last_used = time.time()
        today = _dt.datetime.now(_dt.timezone.utc).date()
        out, cov, pending = [], [], False
        d = max(_dt.datetime.fromtimestamp(t0_ms / 1000, _dt.timezone.utc).date(), today - _dt.timedelta(days=MAX_PAST_DAYS))
        last = min(_dt.datetime.fromtimestamp(t1_ms / 1000, _dt.timezone.utc).date(), today - _dt.timedelta(days=1))
        rest_from = max(t0_ms, now - MAX_REST_HOURS * 3600_000)
        while d <= last:
            a = self._day(d)
            if a == "busy":
                pending = True
            elif a is not None:
                lo, hi = _utc_ms(d), _utc_ms(d) + 86400_000 - 1
                k = (a[0] >= t0_ms) & (a[0] <= t1_ms)
                out.append(tuple(x[k] for x in a))
                cov.append((lo, hi))
            d += _dt.timedelta(days=1)
        day_cov = list(cov)
        if t1_ms >= rest_from:
            with self.lock:
                self.floor_ms = rest_from if self.floor_ms is None else min(self.floor_ms, rest_from)
                segs = [(s.t0, s.t1, list(s.chunks)) for s in self.segs if s.t1 >= t0_ms and s.t0 <= t1_ms]
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
            for s0, s1, chunks in segs:
                parts = [c for c in chunks if c[1][-1] >= t0_ms and c[1][0] <= t1_ms]
                if parts:
                    cols = [np.concatenate([c[i] for c in parts]) for i in range(1, 5)]
                    k = (cols[0] >= t0_ms) & (cols[0] <= t1_ms)
                    for lo, hi in day_cov:          # the day file already holds these: never count a trade twice
                        k &= (cols[0] < lo) | (cols[0] > hi)
                    out.append(tuple(x[k] for x in cols))
                cov.append((s0, s1))
            if not _covers(cov, rest_from, min(t1_ms, now - 2000)):
                pending = True
        cov = _union(cov)
        if not out:
            e = np.array([], "int64")
            return (e, np.array([]), np.array([]), np.array([], "int8")), cov, pending
        cols = [np.concatenate([o[i] for o in out]) for i in range(4)]
        return tuple(cols), cov, pending


def _union(iv):
    iv = sorted(iv)
    out = []
    for a, b in iv:
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _covers(iv, a, b):
    return b < a or any(x <= a and y >= b for x, y in _union(iv))


_stores, _slock = {}, threading.Lock()


def store(sym):
    with _slock:
        if sym not in _stores:
            _stores[sym] = TradeStore(sym)
        return _stores[sym]
