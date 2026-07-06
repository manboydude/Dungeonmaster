# Odrun Fell Discord Bot (v3) — Setup Guide

A full AI-DM Discord bot: Gemini runs the game, and the bot handles dice, full
character sheets, a **combat engine** (monsters that attack back), **initiative**,
**auto level-up**, **slash commands + buttons + rich embeds**, **SQLite** storage,
an optional **AI scene-art** command, and a **live web dashboard** of your party.

## Files
- `bot.py` — the bot (Discord glue, commands, gameplay)
- `game.py` — core rules: characters, dice, checks, XP/leveling, rendering
- `combat.py` — monster stat blocks + attack resolution
- `db.py` — SQLite persistence
- `art.py` — optional AI scene art (off by default)
- `dashboard.py` — the live web dashboard
- `requirements.txt`, `SETUP.md`

Keep all files together in the same folder / repo.

---

## A. Create the Discord bot (~5 min, all clicking)
1. **https://discord.com/developers/applications** → **New Application** → name it → Create.
2. **Bot** → Add Bot / **Reset Token** → **Copy** = your `DISCORD_TOKEN` (keep secret).
3. Same page → **Privileged Gateway Intents** → turn ON **MESSAGE CONTENT INTENT** → Save.
4. **OAuth2 → URL Generator** → Scopes: **bot** and **applications.commands** (the second one enables slash commands). Bot Permissions: **Send Messages**, **Read Message History**, **View Channels**, **Attach Files**. Open the URL, pick your server, Authorize.
5. Make a text channel named exactly **adventure** (lowercase).

## B. Gemini API key (~2 min)
**https://aistudio.google.com/apikey** → Create API key → copy = `GEMINI_API_KEY`.

## C. Host — pick ONE

### Railway (recommended: always-on, web UI, ~$5/mo after trial credit)
1. Upload all the files to a **GitHub repo** (website → Add file → Upload files).
2. **https://railway.app** → sign in with GitHub → **New Project → Deploy from GitHub repo**.
3. **Variables** tab → add `DISCORD_TOKEN` and `GEMINI_API_KEY`.
   Optional: `ART_ENABLED=true` to turn on AI art (needs a paid Gemini tier).
4. **Settings → Deploy → Start Command:** `python bot.py`.
5. **Settings → Networking → Generate Domain** to expose the dashboard. Railway sets `PORT`
   automatically and the bot binds the dashboard to it — that domain is your live party page.

### Your own PC (free; on only when your PC is on)
```
pip install -r requirements.txt
# Windows PowerShell:
$env:DISCORD_TOKEN="..."; $env:GEMINI_API_KEY="..."; python bot.py
```
Dashboard is then at http://localhost:8080.

---

## Commands (every one also works as a `/slash` command for the common ones)

**Dice/checks:** `!roll 1d20+3` · `!check <name> <ability> [prof]`
**Sheet/stats:** `!sheet [name]` (embed with buttons) · `!damage/!heal <name> <n>` ·
`!slot <name> [lvl]` · `!gold/!xp <name> +N` (XP auto-levels) · `!give/!drop <name> <item>` ·
`!condition <name> add|remove <x>` · `!rest <name>`
**Characters:** `!create <name> <race> <class>` (auto-rolls stats) or `/create` for menus ·
`!newchar <name> <maxHP> <AC>` for a blank one
**Combat:** `!spawn <monster>` · `!monsters` · `!attack <pc> <monster>` (bot rolls & applies) ·
`!mattack <monster> <pc>` · `!deathsave <name>` (when downed at 0 HP) —
monsters: goblin, wolf, skeleton, bandit, zombie, giant spider, cultist —
races: human, elf, dwarf, halfling, half-orc, tiefling — classes: fighter, wizard, rogue, cleric, ranger, barbarian
**Turn order:** `!init start` → `!init roll <name> <mod>` → `!init go` · `!next` · `!init end`
**Map:** `!map` · `!map place <name> <x> <y>` · `!map clear`
**Memory:** `!remember <fact>` · `!recap`
**Art:** `!scene <description>` (only if `ART_ENABLED=true`)
**Admin:** `!newchar <name> <maxHP> <AC>` · `!reset` · `!help`

## Notes
- **Slash commands** can take up to an hour to appear the first time (Discord global sync).
  The `!` prefix commands work instantly, so use those until the slashes show up.
- **AI art** is OFF unless you set `ART_ENABLED=true`, so it never costs anything until you
  choose to use it. If you get an "unknown model" error, set `ART_MODEL` to a current image
  model name from Google AI Studio.
- **Spam/cost guard:** each player has a short per-user cooldown on the AI DM (default 3s)
  and on art (default 30s) so a stuck key can't burn your API quota. Tune with the optional
  `COOLDOWN_NARRATIVE` and `COOLDOWN_ART` environment variables (seconds).
- **Death saves:** at 0 HP a character is "down and dying" — roll `!deathsave <name>` each of
  their turns. Three successes stabilizes; three failures is death; a nat 20 revives at 1 HP.
  Healing a downed character brings them back up.
- Each Discord channel is its own separate campaign. Ball Wizard is pre-loaded in each.
- On Railway, add a **Volume** mounted where `dnd.db` lives if you want the game to survive
  redeploys; otherwise a redeploy resets it (characters re-seed automatically).
