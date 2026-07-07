"""
bot.py — Odrun Fell Discord D&D bot (v3).

Ties together: game logic (game.py), combat engine (combat.py), SQLite
persistence (db.py), optional AI art (art.py), and a live web dashboard
(dashboard.py). Offers both classic !prefix commands and modern /slash
commands, with rich embeds and clickable buttons.
"""

import os
import io
import re
import typing
import asyncio

import discord
from discord import app_commands
from google import genai
from google.genai import types

import game
import combat
import db
import art
import dashboard


# ============================ CONFIG ============================
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GAME_CHANNEL = os.environ.get("GAME_CHANNEL", "adventure")
HISTORY_LIMIT = 30
SUMMARIZE_THRESHOLD = 50
PORT = int(os.environ.get("PORT", "8080"))
# When a check/roll happens in the game channel, feed the result to the DM
# automatically so you never have to retype it. Set to "false" to disable.
AUTO_DM_ON_ROLL = os.environ.get("AUTO_DM_ON_ROLL", "true").lower() in ("1", "true", "yes")
# Auto-generate a scene image when the DM signals the party entered a new area.
# Only fires if ART_ENABLED is on. The cooldown caps how often images auto-generate
# per channel, to keep image costs from running away.
AUTO_SCENE_ART = os.environ.get("AUTO_SCENE_ART", "true").lower() in ("1", "true", "yes")
AUTO_SCENE_COOLDOWN = float(os.environ.get("AUTO_SCENE_COOLDOWN", "45"))
# ===============================================================

db.init(os.environ.get("DB_PATH", "dnd.db"))

gemini_client = genai.Client(api_key=GEMINI_API_KEY or "MISSING")

SYSTEM_PROMPT = """You are the Dungeon Master for a beginner-friendly D&D 5e game. Teach \
gently, keep rules notes short, call for specific rolls, and avoid unexplained jargon.

SETTING — ODRUN FELL: a city built on the buried remains of a shattered god-weapon (a \
colossal club) whose relic-bone powers everything above. Lacquered Sprig towers over \
ichor-slick Barrows markets; monster-haunted greatclub tunnels run beneath. Guilds scheme, \
delvers vanish, a soft word can outweigh a blade. Tone: beautiful and rotten.

RUNNING PLAY: describe scenes vividly but briefly, usually ending with "What do you do?". \
Run all NPCs. Respect 5e rules but make fair rulings when unsure; you are not a rules engine.

CALLING FOR ROLLS — use the exact command the bot understands. For a skill, the command is \
`!check NAME skill`, e.g. "Make a Stealth check — type `!check Bungua stealth`" or \
"Make a Perception check — type `!check Ball Wizard perception`". You do NOT need to tell \
players about ability modifiers or proficiency — the bot adds those automatically from the \
character's sheet. Valid skills: acrobatics, animal handling, arcana, athletics, deception, \
history, insight, intimidation, investigation, medicine, nature, perception, performance, \
persuasion, religion, sleight of hand, stealth, survival. For a raw ability, use e.g. \
`!check NAME dex`. After a player rolls, the RESULT IS AUTOMATICALLY SENT TO YOU as a line \
beginning "[dice]" — narrate the outcome from that number; the player does not retype it.

CURRENT STATE IS AUTHORITATIVE: you are given each character's HP, AC, slots, conditions, \
proficient skills, and their exact cantrips/spells, plus live enemies. Treat these as absolute \
truth — never invent numbers, and use the listed spells/skills rather than making up your own. \
When something should change, tell players the command: `!damage NAME N`, `!heal NAME N`, \
`!slot NAME`, `!gold NAME +N`, `!give NAME item`, `!condition NAME add/remove thing`. \
For combat, spawn enemies with `!spawn TYPE` (goblin, wolf, skeleton, bandit, zombie, \
giant spider, cultist, bone-gnawer, ichor-hound, relic-wight). For a creature not on that \
list, tell players `!spawn custom NAME HP AC TOHIT DAMAGE` (e.g. `!spawn custom Ooze 20 8 3 1d6`). \
Then `!attack PC MONSTER` (player hits enemy) and `!mattack MONSTER PC` (enemy hits player) — \
the bot rolls and applies those automatically.

Honor PINNED FACTS and the STORY SUMMARY for continuity. Stay in character, warm and theatrical.

SCENE IMAGES: When the party FIRST arrives in a distinct new location (a new room, cavern, \
street, tavern, chamber, etc.), begin your reply with a tag on its own line in this exact \
form: [SCENE: one vivid sentence describing what the place looks like]. Then continue your \
narration normally. Do this ONLY on genuine arrival somewhere new — never for actions taken \
within the same place, and never more than once per reply. The players never see this tag; \
it silently triggers an illustration of the area."""


# ---- Gemini helpers ---------------------------------------------------------
def _generate(content, system=SYSTEM_PROMPT):
    r = gemini_client.models.generate_content(
        model=GEMINI_MODEL, contents=content,
        config=types.GenerateContentConfig(system_instruction=system))
    return r.text


async def ask_gemini(content, system=SYSTEM_PROMPT):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _generate(content, system))


def build_turn_content(g, player_line):
    pinned = "\n".join(f"- {p}" for p in g["pinned"]) or "(none)"
    history = "\n".join(g["history"][-HISTORY_LIMIT:]) or "(the adventure is just beginning)"
    return (f"CURRENT STATE (authoritative):\n{game.state_for_dm(g)}\n\n"
            f"PINNED FACTS:\n{pinned}\n\nSTORY SUMMARY:\n{g['summary'] or '(none)'}\n\n"
            f"RECENT STORY:\n{history}\n\nNEW PLAYER INPUT:\n{player_line}\n\nRespond as the DM.")


async def maybe_summarize(g):
    if len(g["history"]) <= SUMMARIZE_THRESHOLD:
        return
    old, recent = g["history"][:-HISTORY_LIMIT], g["history"][-HISTORY_LIMIT:]
    prompt = ("Summarize these D&D events into a tight paragraph (plot, NPCs, decisions, open "
              f"threads), merging with the existing summary.\n\nEXISTING:\n{g['summary'] or '(none)'}"
              f"\n\nEVENTS:\n" + "\n".join(old))
    try:
        g["summary"] = (await ask_gemini(prompt, system="You are a concise note-taker.")).strip()
    except Exception:
        pass
    g["history"] = recent


async def dm_react(ch, g, cid, player_line):
    """Send player input (an action, or an auto-relayed roll) to the DM, post the
    response, and persist. Shared by narrative play and auto-roll relay."""
    try:
        async with ch.typing():
            raw = await ask_gemini(build_turn_content(g, player_line))
    except Exception as e:
        await ch.send(f"⚠️ Gemini error: `{e}`"); return
    scene_desc, reply = extract_scene(raw)   # pull out any [SCENE: ...] tag
    g["history"] += [f"Player {player_line}", f"DM: {reply}"]
    await maybe_summarize(g)
    db.save_game(cid, g)
    for i in range(0, len(reply), 1900):
        await ch.send(reply[i:i + 1900])
    await maybe_autoscene(ch, cid, scene_desc)


SCENE_RE = re.compile(r"\[SCENE:\s*(.+?)\]", re.IGNORECASE | re.DOTALL)


def extract_scene(text):
    """Return (scene_description_or_None, text_with_tag_removed)."""
    m = SCENE_RE.search(text)
    desc = m.group(1).strip() if m else None
    return desc, SCENE_RE.sub("", text).strip()


_last_scene = {}


async def maybe_autoscene(ch, cid, scene_desc):
    """Auto-generate an area image if the DM flagged a new location and art is on."""
    if not scene_desc or not AUTO_SCENE_ART or not art.is_enabled():
        return
    now = _time.monotonic()
    if now - _last_scene.get(cid, 0) < AUTO_SCENE_COOLDOWN:
        return   # too soon since the last auto image; skip to control cost
    _last_scene[cid] = now
    try:
        async with ch.typing():
            data = await asyncio.get_event_loop().run_in_executor(None, art.generate_scene, scene_desc)
        await ch.send(file=discord.File(io.BytesIO(data), filename="scene.png"))
    except Exception:
        pass   # never let an image failure interrupt the story


# ---- Embeds + buttons -------------------------------------------------------
GOLD = 0xC9A86A


def sheet_embed(name, c):
    e = discord.Embed(title=name, description=f"{c['class']} · level {c['level']}", color=GOLD)
    e.add_field(name="HP", value=f"{c['hp']}/{c['max_hp']}", inline=True)
    e.add_field(name="AC", value=str(c["ac"]), inline=True)
    e.add_field(name="Prof", value=game.fmt_mod(c["prof_bonus"]), inline=True)
    ab = c["abilities"]
    e.add_field(name="Abilities",
                value="  ".join(f"{k.upper()} {ab[k]}({game.fmt_mod(game.ability_mod(ab[k]))})" for k in game.ABILS),
                inline=False)
    if c.get("skills"):
        e.add_field(name="Skills", value=", ".join(c["skills"]), inline=False)
    e.add_field(name="Spell slots", value=game.slots_str(c), inline=False)
    sp = c.get("spells", {})
    if sp.get("cantrips") or sp.get("prepared"):
        e.add_field(name="Cantrips", value=", ".join(sp.get("cantrips", [])) or "none", inline=False)
        e.add_field(name="Prepared spells", value=", ".join(sp.get("prepared", [])) or "none", inline=False)
    e.add_field(name="Gold / XP", value=f"{c['gold']} gp · {c['xp']} xp", inline=True)
    e.add_field(name="Conditions", value=", ".join(c["conditions"]) or "none", inline=True)
    e.add_field(name="Inventory", value=", ".join(c["inventory"]) or "empty", inline=False)
    if c.get("portrait"):
        e.set_thumbnail(url=c["portrait"])
    return e


class SheetView(discord.ui.View):
    def __init__(self, char_name):
        super().__init__(timeout=None)
        self.char_name = char_name

    @discord.ui.button(label="Roll d20", emoji="🎲", style=discord.ButtonStyle.secondary)
    async def roll_d20(self, interaction, button):
        total, detail = game.roll_expr("1d20")
        await interaction.response.send_message(f"🎲 **{self.char_name}** rolls a d20 → **{total}**", ephemeral=False)

    @discord.ui.button(label="Long Rest", emoji="🌙", style=discord.ButtonStyle.secondary)
    async def long_rest(self, interaction, button):
        async with get_lock(interaction.channel_id):
            g = db.load_game(interaction.channel_id, game.fresh_game)
            c = g["characters"].get(self.char_name)
            if not c:
                await interaction.response.send_message("That character is gone.", ephemeral=True); return
            c["hp"] = c["max_hp"]
            for lvl in c["slots"]:
                c["slots"][lvl][0] = c["slots"][lvl][1]
            c["conditions"] = []
            c["dying"] = None
            db.save_game(interaction.channel_id, g)
        await interaction.response.edit_message(embed=sheet_embed(self.char_name, c), view=self)


# ---- Shared action helpers (used by both prefix and slash) ------------------
def do_damage(g, name, amt, heal=False):
    c = g["characters"][name]
    if heal:
        was_down = c.get("dying") is not None or c["hp"] == 0
        c["hp"] = min(c["max_hp"], c["hp"] + abs(amt))
        c["dying"] = None
        if "dead" in c["conditions"] and c["hp"] > 0:
            c["conditions"].remove("dead")
        return f"💚 {name} heals {abs(amt)} → **HP {c['hp']}/{c['max_hp']}**" + (" — back on their feet." if was_down and c["hp"] > 0 else "")
    c["hp"] = max(0, c["hp"] - abs(amt))
    if c["hp"] == 0:
        if c.get("dying") is None:
            c["dying"] = {"s": 0, "f": 0}
        return f"💥 {name} takes {abs(amt)} → **HP 0 — down and dying!** Roll `!deathsave {name}` on their turn."
    return f"💥 {name} takes {abs(amt)} → **HP {c['hp']}/{c['max_hp']}**"


def do_xp(g, name, delta):
    c = g["characters"][name]
    ups = game.apply_xp(c, delta)
    line = f"⭐ {name} XP → **{c['xp']}**"
    return line + ("\n" + "\n".join(ups) if ups else "")


# ============================ DISCORD ============================
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Per-channel locks: prevent two simultaneous commands in the same channel from
# clobbering each other's load-modify-save on the shared game state.
_locks = {}


def get_lock(cid):
    return _locks.setdefault(cid, asyncio.Lock())


# Per-user cooldowns protect your API quota from spam or a stuck key-repeat.
import time as _time
COOLDOWN_NARRATIVE = float(os.environ.get("COOLDOWN_NARRATIVE", "3"))
COOLDOWN_ART = float(os.environ.get("COOLDOWN_ART", "30"))
_last_call = {}


def on_cooldown(user_id, kind, seconds):
    """Return remaining seconds if on cooldown, else 0 (and start the timer)."""
    now = _time.monotonic()
    key = (user_id, kind)
    if now - _last_call.get(key, 0) < seconds:
        return round(seconds - (now - _last_call.get(key, 0)), 1)
    _last_call[key] = now
    return 0


@client.event
async def on_ready():
    try:
        await tree.sync()
    except Exception as e:
        print("Slash sync issue (prefix commands still work):", e)
    print(f"Logged in as {client.user}. Game channel #{GAME_CHANNEL}. Dashboard on port {PORT}.")


def parse_signed(s):
    s = s.strip().replace(" ", "")
    return int(s) if s.lstrip("+-").isdigit() else None


HELP_TEXT = """**Odrun Fell — commands** (also available as `/slash` commands)
Type actions in **#{ch}**; the AI DM responds.

**Dice/checks:** `!roll 1d20+3` · `!check <name> <skill or ability>` (e.g. `!check Bungua stealth` — proficiency auto-added)
**Spells:** `!spells <name>`
**Sheet/stats:** `!sheet [name]` · `!damage/!heal <name> <n>` · `!slot <name> [lvl]` · `!gold/!xp <name> +N` · `!give/!drop <name> <item>` · `!condition <name> add|remove <x>` · `!rest <name>`
**Characters:** `!create <name> <race> <class>` (rolls stats) or `/create` menus · `!newchar <name> <hp> <ac>`
**Combat:** `!spawn <monster>` (or `!spawn custom <name> <hp> <ac> <tohit> <dmg>`) · `!monsters` · `!attack <pc> <monster>` · `!mattack <monster> <pc>` · `!deathsave <name>`
**Turn order:** `!init start` → `!init roll <name> <mod>` → `!init go` · `!next` · `!init end`
**Map:** `!map` · `!map place <name> <x> <y>` · `!map clear`
**Memory:** `!remember <fact>` · `!recap`
**Extras:** `!scene <desc>` (AI art, if enabled) · `!newchar <name> <hp> <ac>` · `!reset`
Monsters: goblin, wolf, skeleton, bandit, zombie, giant spider, cultist."""


@client.event
async def on_message(message):
    if message.author == client.user or not message.content.strip():
        return
    # Serialize everything happening in one channel so concurrent commands can't
    # clobber each other's saves.
    async with get_lock(message.channel.id):
        await _process_message(message)


async def _process_message(message):
    content = message.content.strip()
    lower = content.lower()
    parts = content.split()
    cid = message.channel.id
    g = db.load_game(cid, game.fresh_game)
    ch = message.channel

    async def send_save(text=None, **kw):
        db.save_game(cid, g)
        if text is not None or kw:
            await ch.send(text, **kw)

    # ---- help ----
    if lower in ("!help", "!commands"):
        await ch.send(HELP_TEXT.format(ch=GAME_CHANNEL)); return

    # ---- dice ----
    if lower.startswith("!roll") or lower.startswith("!r "):
        seg = content.split(None, 1)
        if len(seg) < 2:
            await ch.send("e.g. `!roll 1d20+3`"); return
        total, detail = game.roll_expr(seg[1])
        await ch.send(f"🎲 `{seg[1]}` → [{detail}] = **{total}**" if total is not None else "Couldn't read that.")
        return

    if lower.startswith("!check"):
        if len(parts) < 3:
            await ch.send("`!check <name> <skill or ability> [prof]` — e.g. `!check Bungua stealth`"); return
        toks = parts[1:]
        add_prof = toks[-1].lower() in ("prof", "+prof", "p")
        if add_prof:
            toks = toks[:-1]
        # Match the skill/ability from the TAIL (skills can be up to 3 words),
        # so the remaining leading tokens are the character name.
        name, term = None, None
        for take in (3, 2, 1):
            if len(toks) > take:
                cand_term = " ".join(toks[-take:]).lower()
                cand_name = game.find_char(g, " ".join(toks[:-take]))
                if cand_name and game.resolve_term(g["characters"][cand_name], cand_term):
                    name, term = cand_name, cand_term; break
        if not name:
            await ch.send("Couldn't read that. Try `!check <name> <skill or ability>`.\n"
                          "Skills: " + ", ".join(sorted(game.SKILLS)) + ".")
            return
        total, text, _ = game.roll_check(g["characters"][name], term, add_prof)
        await ch.send(f"**{name}** — 🎲 {text}")
        # Auto-relay the result to the DM so you don't have to retype it.
        if AUTO_DM_ON_ROLL and getattr(ch, "name", None) == GAME_CHANNEL:
            await dm_react(ch, g, cid, f"[dice] {name}'s check result: {text}")
        return

    # ---- sheet (embed + buttons) ----
    if lower == "!sheet":
        for n, c in g["characters"].items():
            await ch.send(embed=sheet_embed(n, c), view=SheetView(n))
        if not g["characters"]:
            await ch.send("No characters yet.")
        return
    if lower.startswith("!sheet"):
        name = game.find_char(g, " ".join(parts[1:]))
        if not name:
            await ch.send("Which character?"); return
        await ch.send(embed=sheet_embed(name, g["characters"][name]), view=SheetView(name)); return

    # ---- hp ----
    if lower.startswith("!damage") or lower.startswith("!heal"):
        if len(parts) < 3:
            await ch.send("`!damage <name> <n>`"); return
        amt = parse_signed(parts[-1]); name = game.find_char(g, " ".join(parts[1:-1]))
        if not name or amt is None:
            await ch.send("Bad character or number."); return
        await send_save(do_damage(g, name, amt, heal=lower.startswith("!heal"))); return

    # ---- slot ----
    if lower.startswith("!slot"):
        if len(parts) < 2:
            await ch.send("`!slot <name> [level]`"); return
        lvl = "1"; np = parts[1:]
        if np and np[-1].isdigit():
            lvl = np[-1]; np = np[:-1]
        name = game.find_char(g, " ".join(np))
        if not name:
            await ch.send("Bad character."); return
        c = g["characters"][name]
        if lvl not in c["slots"]:
            await ch.send(f"{name} has no level-{lvl} slots."); return
        cur, mx = c["slots"][lvl]
        if cur <= 0:
            await ch.send(f"⚠️ {name} out of level-{lvl} slots."); return
        c["slots"][lvl][0] -= 1
        await send_save(f"✨ {name} spends a level-{lvl} slot → **{cur-1}/{mx} left**"); return

    # ---- gold / xp ----
    if lower.startswith("!gold") or lower.startswith("!xp"):
        field = "gold" if lower.startswith("!gold") else "xp"
        if len(parts) < 3:
            await ch.send(f"`!{field} <name> +N`"); return
        delta = parse_signed(parts[-1]); name = game.find_char(g, " ".join(parts[1:-1]))
        if not name or delta is None:
            await ch.send("Bad character or amount."); return
        if field == "gold":
            c = g["characters"][name]; c["gold"] = max(0, c["gold"] + delta)
            await send_save(f"🪙 {name} gold: **{c['gold']}** ({game.fmt_mod(delta)})")
        else:
            await send_save(do_xp(g, name, delta))
        return

    # ---- inventory ----
    if lower.startswith("!give") or lower.startswith("!drop"):
        if len(parts) < 3:
            await ch.send("`!give <name> <item>`"); return
        name = None
        for cut in range(2, len(parts)):
            cand = game.find_char(g, " ".join(parts[1:cut]))
            if cand:
                name, item = cand, " ".join(parts[cut:]); break
        if not name:
            name, item = game.find_char(g, parts[1]), " ".join(parts[2:])
        if not name or not item:
            await ch.send("`!give Ball Wizard Torch`"); return
        c = g["characters"][name]
        if lower.startswith("!give"):
            c["inventory"].append(item); await send_save(f"🎒 {name} gains **{item}**.")
        else:
            match = next((it for it in c["inventory"] if it.lower() == item.lower()), None)
            if not match:
                await ch.send(f"{name} isn't carrying '{item}'."); return
            c["inventory"].remove(match); await send_save(f"🗑️ {name} drops **{match}**.")
        return

    # ---- spells ----
    if lower.startswith("!spells") or lower.startswith("!spell"):
        name = game.find_char(g, " ".join(parts[1:])) if len(parts) > 1 else "Ball Wizard"
        name = game.find_char(g, name) if name else None
        if not name:
            await ch.send("`!spells <name>`"); return
        c = g["characters"][name]; sp = c.get("spells", {})
        if not (sp.get("cantrips") or sp.get("prepared")):
            await ch.send(f"{name} has no spells (not a caster)."); return
        await ch.send(
            f"**{name}'s spells** — slots {game.slots_str(c)}\n"
            f"**Cantrips** (unlimited): {', '.join(sp.get('cantrips', [])) or 'none'}\n"
            f"**Prepared** (cost a slot): {', '.join(sp.get('prepared', [])) or 'none'}")
        return

    # ---- conditions ----
    if lower.startswith("!condition") or lower.startswith("!cond"):
        if len(parts) < 4 or parts[2].lower() not in ("add", "remove", "rm"):
            await ch.send("`!condition <name> add|remove <thing>`"); return
        op = parts[2].lower(); cond = " ".join(parts[3:]).lower(); name = game.find_char(g, parts[1])
        if not name:
            await ch.send("Bad character."); return
        c = g["characters"][name]
        if op == "add":
            if cond not in c["conditions"]:
                c["conditions"].append(cond)
            await send_save(f"⚠️ {name} is now **{cond}**.")
        else:
            if cond in c["conditions"]:
                c["conditions"].remove(cond)
            await send_save(f"✅ {name} no longer {cond}.")
        return

    # ---- rest ----
    if lower.startswith("!rest"):
        name = game.find_char(g, " ".join(parts[1:])) if len(parts) > 1 else None
        if not name:
            await ch.send("`!rest <name>`"); return
        c = g["characters"][name]; c["hp"] = c["max_hp"]
        for lvl in c["slots"]:
            c["slots"][lvl][0] = c["slots"][lvl][1]
        c["conditions"] = []
        await send_save(f"🌙 {name} long-rests → full HP, slots, and clear conditions."); return

    # ---- COMBAT: spawn / monsters / attack / mattack ----
    if lower.startswith("!spawn"):
        if len(parts) < 2:
            await ch.send("`!spawn <monster>` — " + ", ".join(sorted(combat.MONSTERS)) +
                          "\nor `!spawn custom <name> <hp> <ac> <tohit> <dmg>` for a made-up creature."); return
        # Custom creature: !spawn custom Ooze 20 8 3 1d6
        if parts[1].lower() == "custom":
            if len(parts) < 7:
                await ch.send("`!spawn custom <name> <hp> <ac> <tohit> <dmg>` — e.g. `!spawn custom Ooze 20 8 3 1d6`"); return
            dmg = parts[-1]; to_hit = parse_signed(parts[-2]); ac = parse_signed(parts[-3]); hp = parse_signed(parts[-4])
            label = " ".join(parts[2:-4])
            if None in (hp, ac, to_hit) or game.roll_expr(dmg)[0] is None or not label:
                await ch.send("hp/ac/tohit must be numbers and dmg like `1d6+2`."); return
            name = combat.spawn_custom(g, label, hp, ac, to_hit, dmg)
            mob = g["monsters"][name]
            await send_save(f"👹 **{name}** appears! HP {mob['hp']}, AC {mob['ac']}."); return
        # allow two-word types like "giant spider"
        mtype = " ".join(parts[1:])
        name, opts = combat.spawn(g, mtype)
        if name is None:
            await ch.send("Unknown monster. Try: " + ", ".join(opts) + "\nor `!spawn custom <name> <hp> <ac> <tohit> <dmg>`."); return
        mob = g["monsters"][name]
        await send_save(f"👹 **{name}** appears! HP {mob['hp']}, AC {mob['ac']}."); return

    if lower == "!monsters":
        live = combat.living_monsters(g)
        if not live:
            await ch.send("No living enemies."); return
        await ch.send("**Enemies:**\n" + "\n".join(f"- {n}: HP {m['hp']}/{m['max_hp']} AC {m['ac']}" for n, m in live.items()))
        return

    if lower.startswith("!attack"):  # PC hits monster
        if len(parts) < 3:
            await ch.send("`!attack <pc> <monster>`"); return
        finesse = parts[-1].lower() in ("finesse", "dex")
        seg = parts[1:-1] if finesse else parts[1:]
        # split into pc (first matching char) and monster (rest)
        pc = None
        for cut in range(1, len(seg)):
            cand = game.find_char(g, " ".join(seg[:cut]))
            if cand:
                pc, mob_name = cand, " ".join(seg[cut:]); break
        if not pc:
            await ch.send("Couldn't read the attacker."); return
        mob_key = next((k for k in g["monsters"] if k.lower() == mob_name.lower() or mob_name.lower() in k.lower()), None)
        if not mob_key or g["monsters"][mob_key]["hp"] <= 0:
            await ch.send("No such living enemy."); return
        mob = g["monsters"][mob_key]
        hit, crit, dmg, text = combat.pc_basic_attack(g["characters"][pc], pc, mob_key, mob["ac"], finesse)
        if hit:
            mob["hp"] = max(0, mob["hp"] - dmg)
            text += f" {mob_key} at **{mob['hp']}/{mob['max_hp']}**." + (f"  ☠️ **{mob_key} is slain!**" if mob["hp"] == 0 else "")
        await send_save(text); return

    if lower.startswith("!mattack"):  # monster hits PC
        if len(parts) < 3:
            await ch.send("`!mattack <monster> <pc>`"); return
        # monster name may be multi-word; PC is the last matching char
        pc = game.find_char(g, parts[-1])
        mob_name = " ".join(parts[1:-1]) if pc else " ".join(parts[1:])
        mob_key = next((k for k in g["monsters"] if k.lower() == mob_name.lower() or mob_name.lower() in k.lower()), None)
        if not pc:
            # try: last token is pc
            pc = game.find_char(g, parts[-1])
        if not mob_key or not pc:
            await ch.send("`!mattack Goblin Ball Wizard`"); return
        c = g["characters"][pc]
        res = combat.monster_attacks(g, mob_key, pc, c["ac"])
        if res is None:
            await ch.send("That monster can't attack."); return
        hit, crit, dmg, text = res
        if hit:
            c["hp"] = max(0, c["hp"] - dmg)
            text += f" {pc} at **{c['hp']}/{c['max_hp']}**." + (" — they're down!" if c["hp"] == 0 else "")
        await send_save(text); return

    # ---- initiative ----
    if lower.startswith("!init"):
        init = g["initiative"]; sub = parts[1].lower() if len(parts) > 1 else ""
        if sub == "start":
            init["order"], init["turn"], init["active"] = [], 0, False
            await send_save("⚔️ Building initiative. `!init roll <name> <mod>` or `!init add <name> <value>`, then `!init go`.")
        elif sub in ("roll", "add") and len(parts) >= 3:
            mod = parse_signed(parts[-1])
            who = game.find_char(g, " ".join(parts[2:-1])) or " ".join(parts[2:-1])
            if mod is None:
                await ch.send("Need a number."); return
            import random as _r
            val = _r.randint(1, 20) + mod if sub == "roll" else mod
            init["order"].append([who, val]); await send_save(f"➕ {who}: {val}")
        elif sub == "go":
            if not init["order"]:
                await ch.send("Nobody in initiative."); return
            init["order"].sort(key=lambda e: e[1], reverse=True); init["turn"], init["active"] = 0, True
            order = " → ".join(f"{n} ({v})" for n, v in init["order"])
            await send_save(f"⚔️ **Initiative:** {order}\n▶️ First: **{init['order'][0][0]}**")
        elif sub == "end":
            init["active"] = False; await send_save("🏳️ Initiative ended.")
        else:
            await ch.send("`!init start|roll|add|go|end`, `!next`")
        return

    if lower == "!next":
        init = g["initiative"]
        if not init["active"] or not init["order"]:
            await ch.send("No active order."); return
        init["turn"] = (init["turn"] + 1) % len(init["order"])
        tag = "  (top)" if init["turn"] == 0 else ""
        await send_save(f"▶️ Next: **{init['order'][init['turn']][0]}**{tag}"); return

    # ---- map ----
    if lower.startswith("!map"):
        m = g["map"]
        if len(parts) == 1:
            await ch.send(game.render_map(g))
        elif parts[1].lower() == "clear":
            m["tokens"] = {}; await send_save("🗺️ Map cleared.")
        elif parts[1].lower() == "place" and len(parts) >= 5:
            x, y = parse_signed(parts[-2]), parse_signed(parts[-1])
            who = game.find_char(g, " ".join(parts[2:-2])) or " ".join(parts[2:-2])
            if x is None or y is None or not (0 <= x < m["w"] and 0 <= y < m["h"]):
                await ch.send(f"Coords 0–{m['w']-1}."); return
            m["tokens"][who] = [x, y]; await send_save(game.render_map(g))
        else:
            await ch.send("`!map` · `!map place <name> <x> <y>` · `!map clear`")
        return

    # ---- memory ----
    if lower.startswith("!remember"):
        fact = content[len("!remember"):].strip()
        if not fact:
            await ch.send("`!remember <fact>`"); return
        g["pinned"].append(fact); await send_save(f"📌 Pinned: {fact}"); return
    if lower == "!recap":
        pins = "\n".join(f"📌 {p}" for p in g["pinned"]) or "(none)"
        await ch.send(f"**Summary:**\n{g['summary'] or '(none yet)'}\n\n**Pinned:**\n{pins}"[:1950]); return

    # ---- death saves ----
    if lower.startswith("!deathsave") or lower.startswith("!ds"):
        name = game.find_char(g, " ".join(parts[1:])) if len(parts) > 1 else None
        if not name:
            await ch.send("`!deathsave <name>`"); return
        msg, _ = game.death_save(g["characters"][name])
        await send_save(f"**{name}** — {msg}"); return

    # ---- guided character creation ----
    if lower.startswith("!create"):
        # Quick form: !create <name> <race> <class>  (rolls stats for you)
        if len(parts) >= 4:
            cls = parts[-1].lower(); race = parts[-2].lower(); name = " ".join(parts[1:-2])
            if race not in game.RACES or cls not in game.CLASSES:
                await ch.send("Races: " + ", ".join(game.RACES) + "\nClasses: " + ", ".join(game.CLASSES)); return
            c = game.build_character(name, race, cls, game.roll_stats())
            g["characters"][name] = c
            db.save_game(cid, g)
            await ch.send(f"🎲 Rolled up **{name}** the {race.title()} {cls.title()}!",
                          embed=sheet_embed(name, c), view=SheetView(name)); return
        await ch.send("**Create a character:** `!create <name> <race> <class>` and I'll roll the stats.\n"
                      f"Races: {', '.join(game.RACES)}\nClasses: {', '.join(game.CLASSES)}\n"
                      "Prefer menus? Use the `/create` slash command."); return

    # ---- art ----
    if lower.startswith("!scene"):
        desc = content[len("!scene"):].strip()
        if not art.is_enabled():
            await ch.send("🎨 Art is off. Set `ART_ENABLED=true` (and a paid Gemini tier) to use it."); return
        if not desc:
            await ch.send("`!scene <description>`"); return
        wait = on_cooldown(message.author.id, "art", COOLDOWN_ART)
        if wait:
            await ch.send(f"⏳ Art cooldown — {wait}s to go."); return
        await ch.send("🎨 Painting the scene…")
        try:
            data = await asyncio.get_event_loop().run_in_executor(None, art.generate_scene, desc)
            import io
            await ch.send(file=discord.File(io.BytesIO(data), filename="scene.png"))
        except Exception as e:
            await ch.send(f"⚠️ Art failed: `{e}`")
        return

    # ---- admin ----
    if lower.startswith("!newchar"):
        if len(parts) < 4:
            await ch.send("`!newchar <name> <maxHP> <AC>`"); return
        ac = parse_signed(parts[-1]); hp = parse_signed(parts[-2]); name = " ".join(parts[1:-2])
        if hp is None or ac is None or not name:
            await ch.send("HP and AC must be numbers."); return
        g["characters"][name] = game.new_character(hp=hp, ac=ac)
        await send_save(f"🆕 Added **{name}** (HP {hp}, AC {ac})."); return

    if lower == "!reset":
        db.save_game(cid, game.fresh_game())
        await ch.send(f"🔄 This channel's game was reset. Play in #{GAME_CHANNEL}."); return

    # ---- normal narrative play (game channel only) ----
    if getattr(message.channel, "name", None) != GAME_CHANNEL or content.startswith("!"):
        return
    wait = on_cooldown(message.author.id, "narrative", COOLDOWN_NARRATIVE)
    if wait:
        await ch.send(f"⏳ Easy — wait {wait}s before your next action.", delete_after=3); return
    await dm_react(ch, g, cid, f"{message.author.display_name}: {content}")


# ---- Slash commands (parallel to prefix; sync in on_ready) ------------------
@tree.command(name="roll", description="Roll dice, e.g. 1d20+3")
async def s_roll(interaction: discord.Interaction, dice: str):
    total, detail = game.roll_expr(dice)
    await interaction.response.send_message(
        f"🎲 `{dice}` → [{detail}] = **{total}**" if total is not None else "Couldn't read that.")


@tree.command(name="check", description="Skill or ability check (skills auto-add proficiency)")
@app_commands.describe(skill="A skill like stealth/perception, or an ability like dex")
async def s_check(interaction: discord.Interaction, name: str, skill: str, proficient: bool = False):
    g = db.load_game(interaction.channel_id, game.fresh_game)
    cn = game.find_char(g, name)
    if not cn or game.resolve_term(g["characters"][cn], skill) is None:
        await interaction.response.send_message("Bad character or skill/ability.", ephemeral=True); return
    _, text, _ = game.roll_check(g["characters"][cn], skill, proficient)
    await interaction.response.send_message(f"**{cn}** — 🎲 {text}")


@tree.command(name="sheet", description="Show a character sheet")
async def s_sheet(interaction: discord.Interaction, name: str = "Ball Wizard"):
    g = db.load_game(interaction.channel_id, game.fresh_game)
    cn = game.find_char(g, name)
    if not cn:
        await interaction.response.send_message("No such character.", ephemeral=True); return
    await interaction.response.send_message(embed=sheet_embed(cn, g["characters"][cn]), view=SheetView(cn))


@tree.command(name="damage", description="Apply damage to a character")
async def s_damage(interaction: discord.Interaction, name: str, amount: int):
    async with get_lock(interaction.channel_id):
        g = db.load_game(interaction.channel_id, game.fresh_game); cn = game.find_char(g, name)
        if not cn:
            await interaction.response.send_message("No such character.", ephemeral=True); return
        msg = do_damage(g, cn, amount); db.save_game(interaction.channel_id, g)
    await interaction.response.send_message(msg)


@tree.command(name="heal", description="Heal a character")
async def s_heal(interaction: discord.Interaction, name: str, amount: int):
    async with get_lock(interaction.channel_id):
        g = db.load_game(interaction.channel_id, game.fresh_game); cn = game.find_char(g, name)
        if not cn:
            await interaction.response.send_message("No such character.", ephemeral=True); return
        msg = do_damage(g, cn, amount, heal=True); db.save_game(interaction.channel_id, g)
    await interaction.response.send_message(msg)


@tree.command(name="spawn", description="Spawn a monster for combat")
async def s_spawn(interaction: discord.Interaction, monster: str):
    async with get_lock(interaction.channel_id):
        g = db.load_game(interaction.channel_id, game.fresh_game)
        nm, opts = combat.spawn(g, monster)
        if nm is None:
            await interaction.response.send_message("Try: " + ", ".join(opts), ephemeral=True); return
        db.save_game(interaction.channel_id, g)
        mob = g["monsters"][nm]
    await interaction.response.send_message(f"👹 **{nm}** appears! HP {mob['hp']}, AC {mob['ac']}.")


@tree.command(name="create", description="Create a character with menus (stats auto-rolled)")
@app_commands.describe(name="Character name", race="Ancestry", char_class="Class", roll_stats="Roll 4d6 for stats instead of the standard array")
async def s_create(interaction: discord.Interaction, name: str,
                   race: typing.Literal["human", "elf", "dwarf", "halfling", "half-orc", "tiefling"],
                   char_class: typing.Literal["fighter", "wizard", "rogue", "cleric", "ranger", "barbarian"],
                   roll_stats: bool = True):
    async with get_lock(interaction.channel_id):
        g = db.load_game(interaction.channel_id, game.fresh_game)
        scores = game.roll_stats() if roll_stats else None
        c = game.build_character(name, race, char_class, scores)
        g["characters"][name] = c
        db.save_game(interaction.channel_id, g)
    await interaction.response.send_message(
        f"✨ Created **{name}** the {race.title()} {char_class.title()}!",
        embed=sheet_embed(name, c), view=SheetView(name))


@tree.command(name="deathsave", description="Roll a death saving throw for a downed character")
async def s_deathsave(interaction: discord.Interaction, name: str):
    async with get_lock(interaction.channel_id):
        g = db.load_game(interaction.channel_id, game.fresh_game); cn = game.find_char(g, name)
        if not cn:
            await interaction.response.send_message("No such character.", ephemeral=True); return
        msg, _ = game.death_save(g["characters"][cn]); db.save_game(interaction.channel_id, g)
    await interaction.response.send_message(f"**{cn}** — {msg}")


@tree.command(name="scene", description="Generate AI art of a scene (if enabled)")
async def s_scene(interaction: discord.Interaction, description: str):
    if not art.is_enabled():
        await interaction.response.send_message("Art is off (set ART_ENABLED=true).", ephemeral=True); return
    await interaction.response.defer()
    try:
        data = await asyncio.get_event_loop().run_in_executor(None, art.generate_scene, description)
        import io
        await interaction.followup.send(file=discord.File(io.BytesIO(data), filename="scene.png"))
    except Exception as e:
        await interaction.followup.send(f"⚠️ Art failed: `{e}`")


@tree.command(name="help", description="Show all commands")
async def s_help(interaction: discord.Interaction):
    await interaction.response.send_message(HELP_TEXT.format(ch=GAME_CHANNEL), ephemeral=True)


if __name__ == "__main__":
    missing = [n for n, v in (("DISCORD_TOKEN", DISCORD_TOKEN), ("GEMINI_API_KEY", GEMINI_API_KEY)) if not v]
    if missing:
        raise SystemExit(f"Missing env var(s): {', '.join(missing)}")
    dashboard.start(PORT)   # live web dashboard in a background thread
    client.run(DISCORD_TOKEN)
