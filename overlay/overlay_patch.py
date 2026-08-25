#!/usr/bin/env python3
"""
Adds a third mode — the live agent betting board — to the nightly overlay.

Per the show format: one overlay file, one browser source. A recurring segment
gets a palette block under body[data-mode="..."] and a panel view; it does not
get a second file. This reuses the Scoreboard's table panel and renderer whole,
so the only new code is the palette, a poller, and three edited lines.

    python3 overlay_patch.py <overlay-template.html> -o <patched.html>
"""
import argparse, re, sys

PALETTE = '''
/* ================================================================
   LIVE MODE - agents betting in real time (kb1).
   A third world, deliberately unlike the other two: news is violet
   and gold, the Scoreboard is a black-and-green terminal, this is
   deep indigo and hot orange. The viewer should never have to be
   told which part of the show they are in.
   ================================================================ */
body[data-mode="bets"] {
  --bg:     rgba(7, 8, 26, 0.95);
  --bg2:    rgba(13, 15, 38, 0.88);
  --accent: #ff7a29;
  --accent2:#ffd166;
  --rail:   linear-gradient(180deg, #ffd166 0%, #ff7a29 55%, #c2410c 100%);
  --text:   #f2eeff;
  --dim:    #7b7aa6;
  --stroke: rgba(255, 122, 41, 0.32);
  --glow:   rgba(255, 122, 41, 0.22);
}
body[data-mode="bets"] .card{ border-radius:4px; }
body[data-mode="bets"] #statv{ color:#ff7a29; }
body[data-mode="bets"] .num{
  font-family:'JetBrains Mono','SF Mono',ui-monospace,Menlo,Consolas,monospace;
}
body[data-mode="bets"] #board{
  background:linear-gradient(180deg, rgba(7,8,26,.97), rgba(11,13,34,.95));
  border-color:rgba(255,122,41,.30);
  box-shadow:0 24px 60px rgba(0,0,0,.66), 0 0 0 1px rgba(0,0,0,.4),
             inset 0 1px 0 rgba(255,209,102,.10);
}

/* the panel's greens are baked into #board's children, so restate them */
body[data-mode="bets"] #board .bhead{
  border-bottom-color:rgba(255,122,41,.20);
  background:linear-gradient(90deg, rgba(255,122,41,.12), rgba(255,122,41,0) 62%);
}
body[data-mode="bets"] #board .bmark{ font-size:0; }
body[data-mode="bets"] #board .bmark::after{
  content:"LIVE ROUND"; font-size:11px; font-weight:700;
  letter-spacing:.20em; color:#ff7a29;
}
body[data-mode="bets"] #board .blip{ background:#ff7a29; box-shadow:0 0 8px rgba(255,122,41,.8); }
body[data-mode="bets"] #board .bsrc{ color:#8a86b8; }
body[data-mode="bets"] #board .btitle{ color:#f6f2ff; }
body[data-mode="bets"] #board th{ color:#7b7aa6; border-bottom-color:rgba(255,122,41,.14); }
body[data-mode="bets"] #board td{ color:#ded9f5; }
body[data-mode="bets"] #board td.rank{ color:#ff7a29; }
body[data-mode="bets"] #board td.sub{ color:#7b7aa6; }
body[data-mode="bets"] #board td.pos{ color:#ffd166; }
body[data-mode="bets"] #board td.neg{ color:#ff5c72; }
body[data-mode="bets"] #board tr.hi td{ background:rgba(255,122,41,.10); }
body[data-mode="bets"] #board tr.hi td.who{ color:#ffd166; }
body[data-mode="bets"] #board .bar{ background:linear-gradient(90deg,#c2410c,#ff9433); }
body[data-mode="bets"] #board .bfoot{
  color:#7b7aa6; border-top-color:rgba(255,122,41,.16);
}
body[data-mode="bets"] #board .bfoot b{ color:#ffd166; }

/* the round state line pulses only while betting is open */
body[data-mode="bets"] #bfoot b{ color:#ffd166; }
body[data-mode="bets"].live-open #bfoot div:first-child b:first-child{
  animation:kb1pulse 1.6s ease-in-out infinite;
}
@keyframes kb1pulse{ 0%,100%{opacity:1} 50%{opacity:.45} }
@media (prefers-reduced-motion: reduce){
  body[data-mode="bets"].live-open #bfoot div:first-child b:first-child{ animation:none; }
}
'''

POLLER = '''
/* ================================================================
   LIVE MODE - poll the ringmaster's localhost shim.
   technocore ships CORS default-deny, so a browser source cannot read
   a room directly. ringmaster.py long-polls the room and re-serves the
   board on 127.0.0.1, which is the origin we are allowed to read.
   ================================================================ */
const KB1_URL  = 'http://127.0.0.1:8787/board.json';
const KB1_POLL = 1500;
let KB1 = null, kb1Timer = null;

async function kb1Pull(){
  try{
    const r = await fetch(KB1_URL, {cache:'no-store'});
    if (r.ok) KB1 = await r.json();
  }catch(e){ /* ringmaster not up: hold the last good board, never blank on air */ }
  if (document.body.dataset.mode === 'bets') kb1Paint();
}

function kb1Paint(){
  if (!KB1 || !KB1.views || !KB1.views.live) return;
  const live = KB1.live || {};
  document.body.classList.toggle('live-open', live.state === 'BETTING OPEN');
  paintBoard({ view: KB1.views.live, meta: KB1.meta });
}

function kb1Start(){ if (!kb1Timer){ kb1Pull(); kb1Timer = setInterval(kb1Pull, KB1_POLL); } }
function kb1Stop(){ clearInterval(kb1Timer); kb1Timer = null; }
'''

def patch(src: str) -> str:
    # 1. palette, right after the Scoreboard palette block
    anchor = 'body[data-mode="score"] .num{'
    i = src.index(anchor)
    j = src.index('}', src.index('}', i) + 1) + 1
    src = src[:j] + "\n" + PALETTE + src[j:]

    # 2. paintBoard accepts a live view object as well as a named one
    old = """function paintBoard(viewName){
  const D = (typeof SCOREBOARD !== 'undefined') ? SCOREBOARD : null;
  const v = D && D.views ? D.views[viewName] : null;
  if (!v){ boardEl.classList.remove('on'); return; }

  $('btitle').textContent = v.title || '';
  $('bsrc').textContent   = (D.meta && D.meta.source ? D.meta.source : '') +
                            (D.meta && D.meta.window ? ' · ' + D.meta.window : '');"""
    new = """function paintBoard(viewName){
  const D = (typeof SCOREBOARD !== 'undefined') ? SCOREBOARD : null;
  // A string names a table in SCOREBOARD.views (the nightly Scoreboard).
  // An object is a live view handed straight in (the kb1 betting board).
  let v = D && D.views ? D.views[viewName] : null;
  let meta = D ? D.meta : null;
  if (viewName && typeof viewName === 'object'){ v = viewName.view; meta = viewName.meta; }
  if (!v){ boardEl.classList.remove('on'); return; }

  $('btitle').textContent = v.title || '';
  $('bsrc').textContent   = (meta && meta.source ? meta.source : '') +
                            (meta && meta.window ? ' · ' + meta.window : '');"""
    if old not in src:
        sys.exit("paintBoard signature not found — the template changed; patch by hand")
    src = src.replace(old, new)

    # 3. poller, just before syncMode
    src = src.replace("function syncMode(){", POLLER + "\nfunction syncMode(){", 1)

    # 4. syncMode learns the third mode
    old = """function syncMode(){
  const t = TOPICS[idx];
  const score = t && t.mode === 'score';
  document.body.dataset.mode = score ? 'score' : 'news';
  if (score) paintBoard(t.view); else boardEl.classList.remove('on');
}"""
    new = """function syncMode(){
  const t = TOPICS[idx];
  const m = (t && t.mode) || 'news';
  document.body.dataset.mode = (m === 'score' || m === 'bets') ? m : 'news';
  if (m === 'bets'){ kb1Start(); kb1Paint(); return; }
  kb1Stop();
  document.body.classList.remove('live-open');
  if (m === 'score') paintBoard(t.view); else boardEl.classList.remove('on');
}"""
    if old not in src:
        sys.exit("syncMode not found — the template changed; patch by hand")
    return src.replace(old, new)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    out = patch(open(a.template, encoding="utf-8").read())
    open(a.out, "w", encoding="utf-8").write(out)
    print(f"patched → {a.out}")
