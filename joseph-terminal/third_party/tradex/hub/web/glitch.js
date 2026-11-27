// TradeX web beta: sign-in glitch (2026-11-25). A short screen tear + colour split on the real page and a sci-fi
// "access granted" sound made in the browser (Web Audio, no audio file). Used by web/signin.html (on the sign-in click,
// where browsers allow sound) and by web/auth_web.js (visual when the chart opens after sign-in).
// TXGlitch.play({ sound: true, ms: 700 }) -> Promise that resolves when the effect ends.
// Off switches: localStorage "txSfx" = "off" (no sound); system "reduce motion" = no screen effect.
(function () {
  "use strict";
  if (window.TXGlitch) return;
  const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const muted = () => { try { return localStorage.getItem("txSfx") === "off"; } catch (_) { return false; } };
  let actx = null;

  // ---------- sound ----------
  function sound() {
    if (muted()) return;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      if (actx.state === "suspended") actx.resume();
      const a = actx, t0 = a.currentTime + 0.02;
      const out = a.createDynamicsCompressor(), master = a.createGain();
      master.gain.value = 0.34; master.connect(out); out.connect(a.destination);
      // 1) digital noise in stutters (bitcrushed by a stepped band-pass)
      const len = Math.floor(a.sampleRate * 0.5), buf = a.createBuffer(1, len, a.sampleRate), d = buf.getChannelData(0);
      let hold = 0; for (let i = 0; i < len; i++) { if (i % 24 === 0) hold = Math.random() * 2 - 1; d[i] = hold; }
      const nz = a.createBufferSource(), bp = a.createBiquadFilter(), ng = a.createGain();
      nz.buffer = buf; bp.type = "bandpass"; bp.Q.value = 6; ng.gain.value = 0;
      nz.connect(bp); bp.connect(ng); ng.connect(master);
      let t = t0;
      for (let k = 0; k < 9; k++) {
        const on = 0.018 + Math.random() * 0.03, off = 0.01 + Math.random() * 0.03;
        bp.frequency.setValueAtTime(400 + Math.random() * 5200, t);
        ng.gain.setValueAtTime(0.55 + Math.random() * 0.4, t); ng.gain.setValueAtTime(0, t + on); t += on + off;
      }
      nz.start(t0); nz.stop(t0 + 0.5);
      // 2) falling "data" chirp in steps (square wave)
      const sq = a.createOscillator(), sg = a.createGain(), lp = a.createBiquadFilter();
      sq.type = "square"; lp.type = "lowpass"; lp.frequency.value = 3200; sg.gain.value = 0;
      sq.connect(lp); lp.connect(sg); sg.connect(master);
      const c0 = t0 + 0.05;
      for (let k = 0; k < 12; k++) sq.frequency.setValueAtTime(1800 * Math.pow(0.78, k) * (0.9 + Math.random() * 0.2), c0 + k * 0.028);
      sg.gain.setValueAtTime(0.12, c0); sg.gain.exponentialRampToValueAtTime(0.001, c0 + 0.36);
      sq.start(c0); sq.stop(c0 + 0.4);
      // 3) low thump as the screen settles
      const th = a.createOscillator(), tg = a.createGain(), k0 = t0 + 0.42;
      th.type = "sine"; th.frequency.setValueAtTime(110, k0); th.frequency.exponentialRampToValueAtTime(42, k0 + 0.25);
      tg.gain.setValueAtTime(0.0001, k0); tg.gain.exponentialRampToValueAtTime(0.9, k0 + 0.01); tg.gain.exponentialRampToValueAtTime(0.001, k0 + 0.32);
      th.connect(tg); tg.connect(master); th.start(k0); th.stop(k0 + 0.35);
      // 4) "lock-in" ping (two quick high tones, the second a fifth up)
      [[1320, 0.46], [1980, 0.52]].forEach(([f, at]) => {
        const p = a.createOscillator(), pg = a.createGain(), s = t0 + at;
        p.type = "sine"; p.frequency.value = f; pg.gain.setValueAtTime(0.0001, s);
        pg.gain.exponentialRampToValueAtTime(0.22, s + 0.006); pg.gain.exponentialRampToValueAtTime(0.001, s + 0.16);
        p.connect(pg); pg.connect(master); p.start(s); p.stop(s + 0.18);
      });
    } catch (_) { /* no audio in this browser: the screen effect still plays */ }
  }

  // ---------- screen ----------
  const NS = "http://www.w3.org/2000/svg";
  function filterEl() {
    let svg = document.getElementById("txg-svg");
    if (svg) return svg;
    svg = document.createElementNS(NS, "svg"); svg.id = "txg-svg"; svg.setAttribute("width", "0"); svg.setAttribute("height", "0");
    svg.style.cssText = "position:fixed;width:0;height:0;pointer-events:none";
    // Horizontal tear (turbulence stretched into bands -> displacement) + red / cyan split of the torn page.
    svg.innerHTML = `<filter id="txg" x="-5%" y="-5%" width="110%" height="110%" color-interpolation-filters="sRGB">
      <feTurbulence id="txg-n" type="fractalNoise" baseFrequency="0.000001 0.06" numOctaves="1" seed="1" result="n"/>
      <feDisplacementMap id="txg-d" in="SourceGraphic" in2="n" scale="0" xChannelSelector="R" yChannelSelector="A" result="t"/>
      <feColorMatrix in="t" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r"/>
      <feOffset id="txg-r" in="r" dx="0" dy="0" result="ro"/>
      <feColorMatrix in="t" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 1 0" result="c"/>
      <feOffset id="txg-c" in="c" dx="0" dy="0" result="co"/>
      <feBlend in="ro" in2="co" mode="screen"/></filter>`;
    document.body.appendChild(svg);
    return svg;
  }
  function overlay() {
    const o = document.createElement("div");
    o.style.cssText = "position:fixed;inset:0;z-index:2147483647;pointer-events:none;mix-blend-mode:screen;opacity:0;" +
      "background:repeating-linear-gradient(0deg,rgba(232,236,238,.07) 0 1px,transparent 1px 3px)";
    document.body.appendChild(o);
    return o;
  }
  function screen(ms) {
    return new Promise((done) => {
      if (reduce() || !document.body) return done();
      filterEl();
      const root = document.documentElement, prev = root.style.filter, o = overlay();
      const n = document.getElementById("txg-n"), dm = document.getElementById("txg-d"), r = document.getElementById("txg-r"), c = document.getElementById("txg-c");
      root.style.filter = "url(#txg)";
      const start = performance.now();
      // Bursts, not a constant shake: strong at the start, a calm gap, one last kick, then clean.
      const strength = (p) => (p < 0.34 ? 1 - p * 1.2 : p < 0.55 ? 0.08 : p < 0.72 ? 0.85 : Math.max(0, 1 - (p - 0.72) * 5));
      let last = 0;
      function frame(now) {
        const p = (now - start) / ms;
        if (p >= 1) { root.style.filter = prev; o.remove(); return done(); }
        if (now - last > 45) {   // new tear pattern ~22 times a second (glitches look stepped, not smooth)
          last = now;
          const s = strength(p), j = () => (Math.random() * 2 - 1) * s;
          n.setAttribute("seed", String(1 + Math.floor(Math.random() * 999)));
          n.setAttribute("baseFrequency", `0.000001 ${(0.02 + Math.random() * 0.09).toFixed(3)}`);
          dm.setAttribute("scale", String(Math.round(Math.random() < 0.8 ? 70 * s : 0)));
          r.setAttribute("dx", (6 * j() - 3 * s).toFixed(1)); c.setAttribute("dx", (6 * j() + 3 * s).toFixed(1));
          r.setAttribute("dy", (1.5 * j()).toFixed(1));
          o.style.opacity = String(Math.min(0.9, s * (0.4 + Math.random() * 0.6)));
          o.style.transform = `translateY(${Math.round(Math.random() * 3)}px)`;
        }
        requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }

  // Can this page play sound right now without a click? (Chrome allows it on sites a person uses often.)
  function soundAllowed() {
    if (muted()) return true;   // nothing to unlock
    try {
      if (navigator.getAutoplayPolicy) return navigator.getAutoplayPolicy("audiocontext") === "allowed";
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      return actx.state === "running";
    } catch (_) { return false; }
  }
  // "Enter TradeX" welcome screen. Browsers block sound until the visitor clicks or presses a key, so the chart opens
  // behind this screen and the click on it plays the glitch WITH sound (Saifullah 2026-11-25: sound every time).
  // Skipped when the browser already allows sound.
  function enter(who) {
    if (soundAllowed()) return window.TXGlitch.play({ ms: 650 });
    return new Promise((done) => {
      const w = who || {}, esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
      const g = document.createElement("div"); g.id = "txenter";
      g.innerHTML = `<style>
        #txenter{position:fixed;inset:0;z-index:2147483646;display:grid;place-items:center;cursor:pointer;
          background:radial-gradient(ellipse at 50% 45%,rgba(19,20,22,.82),rgba(10,10,11,.96));backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
          font:14px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif;color:#b4bcbf;transition:opacity .35s ease}
        #txenter .in{display:flex;flex-direction:column;align-items:center;gap:14px;text-align:center;padding:24px}
        #txenter img.logo{width:72px;height:72px;border-radius:16px;box-shadow:0 0 0 1px #2a2d31,0 20px 50px rgba(0,0,0,.6)}
        #txenter h2{margin:6px 0 0;color:#e8ecee;font-size:22px;font-weight:650;letter-spacing:-.01em}
        #txenter button{margin-top:8px;height:44px;padding:0 26px;border-radius:10px;cursor:pointer;background:#e8ecee;color:#0a0a0b;
          border:0;font:600 14px inherit;font-family:inherit;letter-spacing:.02em;box-shadow:0 0 0 1px #e8ecee,0 0 24px rgba(232,236,238,.18)}
        #txenter button:hover{background:#fff}#txenter small{color:#797e86;font-size:12px}
        </style><div class="in"><img class="logo" src="web/brand/icon-256.png" alt="">
        <h2>${w.name ? "Welcome back, " + esc(w.name) : "Welcome to TradeX"}</h2>
        <button type="button" autofocus>Enter TradeX</button><small>Click or press Enter</small></div>`;
      document.body.appendChild(g);
      const b = g.querySelector("button"); try { b.focus(); } catch (_) {}
      const go = (e) => {
        if (e && e.type === "keydown" && !["Enter", " ", "Escape"].includes(e.key)) return;
        if (e) e.preventDefault();
        g.removeEventListener("click", go); removeEventListener("keydown", go, true);
        const p = window.TXGlitch.play({ ms: 700 });   // inside the click: sound allowed
        g.style.opacity = "0"; setTimeout(() => g.remove(), 380);
        p.then(done);
      };
      g.addEventListener("click", go); addEventListener("keydown", go, true);
    });
  }

  window.TXGlitch = {
    enter,
    play(opts) {
      const o = opts || {}, ms = o.ms || 700;
      try { sessionStorage.setItem("txFxAt", String(Date.now())); } catch (_) {}
      if (o.sound !== false) sound(); return screen(ms);
    },
  };
})();
