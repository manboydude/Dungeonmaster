"""
dashboard.py — a live web page showing every party's character sheets, reading
from the same SQLite DB the bot writes to. Runs in a background thread inside
the bot process and auto-refreshes every 10 seconds.

On Railway it binds to the platform's PORT, so the service's public URL becomes
your live game dashboard.
"""

import threading
from flask import Flask
import db
from game import ability_mod, fmt_mod, ABILS, slots_str, inv_str

app = Flask(__name__)

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10">
<title>Odrun Fell — Live Party</title>
<style>
  body {{ background:#12100f; color:#e8e0d0; font-family:'Georgia',serif; margin:0; padding:2rem; }}
  h1 {{ font-variant:small-caps; letter-spacing:2px; color:#c9a86a; border-bottom:1px solid #3a332a; padding-bottom:.5rem; }}
  h2 {{ color:#9a8050; font-size:1rem; text-transform:uppercase; letter-spacing:3px; margin-top:2rem; }}
  .grid {{ display:flex; flex-wrap:wrap; gap:1rem; }}
  .card {{ background:#1c1915; border:1px solid #3a332a; border-radius:10px; padding:1rem 1.25rem; width:320px; box-shadow:0 4px 16px #0006; }}
  .name {{ font-size:1.3rem; color:#e8d5a8; }}
  .sub {{ color:#8a7f6a; font-size:.85rem; margin-bottom:.75rem; }}
  .bar {{ height:14px; background:#2a2620; border-radius:7px; overflow:hidden; margin:.3rem 0 .75rem; }}
  .fill {{ height:100%; background:linear-gradient(90deg,#7a2d2d,#b34a4a); }}
  .abils {{ display:flex; gap:.4rem; flex-wrap:wrap; margin:.5rem 0; }}
  .ab {{ background:#26221c; border-radius:6px; padding:.25rem .5rem; font-size:.8rem; text-align:center; min-width:52px; }}
  .ab b {{ display:block; color:#c9a86a; }}
  .line {{ font-size:.85rem; color:#c8bfa8; margin:.2rem 0; }}
  .foot {{ color:#5a5347; font-size:.75rem; margin-top:2rem; }}
</style></head><body>
<h1>Odrun Fell — Live Party Dashboard</h1>
{body}
<div class="foot">Auto-refreshes every 10s · powered by your Discord bot</div>
</body></html>"""


def _card(name, c):
    pct = int(100 * c["hp"] / c["max_hp"]) if c["max_hp"] else 0
    abils = "".join(
        f'<div class="ab"><b>{k.upper()}</b>{c["abilities"][k]} ({fmt_mod(ability_mod(c["abilities"][k]))})</div>'
        for k in ABILS)
    return f"""<div class="card">
      <div class="name">{name}</div>
      <div class="sub">{c['class']} · level {c['level']} · AC {c['ac']}</div>
      <div class="line">HP {c['hp']} / {c['max_hp']}</div>
      <div class="bar"><div class="fill" style="width:{pct}%"></div></div>
      <div class="abils">{abils}</div>
      <div class="line">Spell slots: {slots_str(c)}</div>
      <div class="line">Gold {c['gold']} · XP {c['xp']} · Prof {fmt_mod(c['prof_bonus'])}</div>
      <div class="line">Conditions: {', '.join(c['conditions']) or 'none'}</div>
      <div class="line">Inventory: {inv_str(c)}</div>
    </div>"""


@app.route("/")
def index():
    games = db.all_games()
    if not games:
        body = "<p>No active games yet. Start playing in Discord!</p>"
    else:
        blocks = []
        for cid, g in games:
            cards = "".join(_card(n, c) for n, c in g["characters"].items())
            blocks.append(f'<h2>Channel {cid}</h2><div class="grid">{cards}</div>')
        body = "".join(blocks)
    return PAGE.format(body=body)


def start(port):
    def run():
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
