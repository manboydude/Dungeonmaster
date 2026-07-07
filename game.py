"""
game.py — pure game logic, no Discord or network dependencies.
Everything here is unit-testable on its own.
"""

import re
import random

ABILS = {"str": "Strength", "dex": "Dexterity", "con": "Constitution",
         "int": "Intelligence", "wis": "Wisdom", "cha": "Charisma"}

# Each skill maps to the ability it uses.
SKILLS = {
    "acrobatics": "dex", "animal handling": "wis", "arcana": "int", "athletics": "str",
    "deception": "cha", "history": "int", "insight": "wis", "intimidation": "cha",
    "investigation": "int", "medicine": "wis", "nature": "int", "perception": "wis",
    "performance": "cha", "persuasion": "cha", "religion": "int", "sleight of hand": "dex",
    "stealth": "dex", "survival": "wis",
}
_ABIL_FULL = {v.lower(): k for k, v in ABILS.items()}  # "strength" -> "str"

# 5e experience thresholds (index = level).
XP_THRESHOLDS = [0, 0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
                 85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000,
                 305000, 355000]


def prof_for_level(level):
    return 2 + (level - 1) // 4


def level_for_xp(xp):
    lvl = 1
    for l in range(2, len(XP_THRESHOLDS)):
        if xp >= XP_THRESHOLDS[l]:
            lvl = l
    return lvl


def ability_mod(score):
    return (score - 10) // 2


def fmt_mod(m):
    return f"+{m}" if m >= 0 else str(m)


def new_character(cls="Adventurer", level=1, ac=10, hp=8, hit_die=8,
                  abilities=None, slots=None, inventory=None, notes=""):
    return {
        "class": cls, "level": level, "ac": ac,
        "hp": hp, "max_hp": hp, "hit_die": hit_die,
        "abilities": abilities or {k: 10 for k in ABILS},
        "prof_bonus": prof_for_level(level),
        "slots": slots or {}, "gold": 0, "xp": XP_THRESHOLDS[level],
        "inventory": inventory or [], "conditions": [], "notes": "",
        "portrait": None,
        "dying": None,   # None, or {"s": successes, "f": failures}
        "skills": [],    # proficient skill names, e.g. ["Stealth", "Perception"]
        "spells": {"cantrips": [], "prepared": []},
    }


# ---- Character creation content --------------------------------------------
STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

RACES = {
    "human":    {"str": 1, "dex": 1, "con": 1, "int": 1, "wis": 1, "cha": 1},
    "elf":      {"dex": 2, "int": 1},
    "dwarf":    {"con": 2, "wis": 1},
    "halfling": {"dex": 2, "cha": 1},
    "half-orc": {"str": 2, "con": 1},
    "tiefling": {"cha": 2, "int": 1},
}

# Sensible default skill proficiencies per class (since !create doesn't ask).
CLASS_SKILLS = {
    "fighter":   ["Athletics", "Perception", "Intimidation", "Survival"],
    "wizard":    ["Arcana", "History", "Investigation", "Insight"],
    "rogue":     ["Stealth", "Sleight of Hand", "Perception", "Acrobatics", "Investigation", "Deception"],
    "cleric":    ["Insight", "Medicine", "Religion", "Persuasion"],
    "ranger":    ["Stealth", "Perception", "Survival", "Animal Handling", "Nature"],
    "barbarian": ["Athletics", "Perception", "Intimidation", "Survival"],
}

# Default level-1 spell loadouts for casters.
CLASS_SPELLS = {
    "wizard": {"cantrips": ["Fire Bolt", "Mage Hand", "Prestidigitation"],
               "prepared": ["Magic Missile", "Shield", "Detect Magic", "Sleep", "Feather Fall"]},
    "cleric": {"cantrips": ["Sacred Flame", "Guidance", "Light"],
               "prepared": ["Cure Wounds", "Bless", "Guiding Bolt", "Shield of Faith"]},
}

CLASSES = {
    "fighter": {"hit_die": 10, "priority": ["str", "con", "dex", "wis", "cha", "int"],
                "ac": {"base": 16, "dex": False}, "slots": {},
                "kit": ["Longsword", "Shield", "Chain mail", "Explorer's pack"]},
    "wizard":  {"hit_die": 6, "priority": ["int", "con", "dex", "wis", "cha", "str"],
                "ac": {"base": 10, "dex": True}, "slots": {"1": [2, 2]},
                "kit": ["Quarterstaff", "Spellbook", "Component pouch", "Scholar's pack"]},
    "rogue":   {"hit_die": 8, "priority": ["dex", "con", "int", "wis", "cha", "str"],
                "ac": {"base": 11, "dex": True}, "slots": {},
                "kit": ["Shortsword", "Shortbow", "Thieves' tools", "Leather armor"]},
    "cleric":  {"hit_die": 8, "priority": ["wis", "con", "str", "dex", "int", "cha"],
                "ac": {"base": 16, "dex": False}, "slots": {"1": [2, 2]},
                "kit": ["Mace", "Shield", "Scale mail", "Priest's pack"]},
    "ranger":  {"hit_die": 10, "priority": ["dex", "wis", "con", "str", "int", "cha"],
                "ac": {"base": 11, "dex": True}, "slots": {},
                "kit": ["Longbow", "Shortsword", "Leather armor", "Explorer's pack"]},
    "barbarian": {"hit_die": 12, "priority": ["str", "con", "dex", "wis", "cha", "int"],
                  "ac": {"base": 10, "dex": True}, "slots": {},
                  "kit": ["Greataxe", "Handaxe", "Explorer's pack"]},
}


def roll_stats():
    """Roll six ability scores, 4d6 drop lowest each."""
    out = []
    for _ in range(6):
        dice = sorted(random.randint(1, 6) for _ in range(4))[1:]
        out.append(sum(dice))
    return sorted(out, reverse=True)


def build_character(name, race, cls, scores=None):
    """Assemble a full level-1 character from race + class + six scores."""
    race, cls = race.lower(), cls.lower()
    if race not in RACES or cls not in CLASSES:
        return None
    conf = CLASSES[cls]
    scores = sorted(scores or STANDARD_ARRAY, reverse=True)[:6]
    while len(scores) < 6:
        scores.append(8)
    c = new_character(cls=cls.title(), hit_die=conf["hit_die"],
                      slots={k: list(v) for k, v in conf["slots"].items()},
                      inventory=list(conf["kit"]))
    # assign highest scores to the class's priority abilities
    for ab, val in zip(conf["priority"], scores):
        c["abilities"][ab] = val
    # racial bonuses
    for ab, bonus in RACES[race].items():
        c["abilities"][ab] += bonus
    # derived HP and AC
    c["max_hp"] = c["hp"] = max(1, conf["hit_die"] + ability_mod(c["abilities"]["con"]))
    c["ac"] = conf["ac"]["base"] + (ability_mod(c["abilities"]["dex"]) if conf["ac"]["dex"] else 0)
    c["skills"] = list(CLASS_SKILLS.get(cls, []))
    sp = CLASS_SPELLS.get(cls)
    c["spells"] = {"cantrips": list(sp["cantrips"]), "prepared": list(sp["prepared"])} if sp else {"cantrips": [], "prepared": []}
    c["notes"] = f"{race.title()} {cls.title()}"
    return c


def death_save(char):
    """Roll one death saving throw. Returns (message, resolved) where resolved
    is True if the character is now stable, revived, or dead."""
    if char["hp"] > 0:
        return f"{char['class']} isn't dying.", False
    if char["dying"] is None:
        char["dying"] = {"s": 0, "f": 0}
    d = random.randint(1, 20)
    st = char["dying"]
    if d == 20:
        char["hp"] = 1
        char["dying"] = None
        return f"🎲 d20(**20**) — 💫 **regains consciousness at 1 HP!**", True
    if d == 1:
        st["f"] += 2
        tag = "💀 **nat 1 — two failures!**"
    elif d >= 10:
        st["s"] += 1
        tag = "✅ success"
    else:
        st["f"] += 1
        tag = "❌ failure"
    line = f"🎲 d20({d}) — {tag}  (successes {st['s']}/3, failures {st['f']}/3)"
    if st["f"] >= 3:
        char["dying"] = None
        if "dead" not in char["conditions"]:
            char["conditions"].append("dead")
        return line + "\n☠️ **They have died.**", True
    if st["s"] >= 3:
        char["dying"] = None
        return line + "\n🩹 **Stabilized** (unconscious but no longer dying).", True
    return line, False


def fresh_game():
    ball = new_character(
        cls="Wizard", level=1, ac=12, hp=8, hit_die=6,
        abilities={"str": 8, "dex": 14, "con": 13, "int": 15, "wis": 12, "cha": 10},
        slots={"1": [2, 2]},
        inventory=["Quarterstaff", "Spellbook", "Component pouch", "Scholar's pack", "Lens on a chain"],
    )
    ball["notes"] = "Human Wizard, Sage background. Aiming to become a Gandalf-style archmage."
    ball["skills"] = ["Arcana", "History", "Investigation", "Insight"]
    ball["spells"] = {"cantrips": ["Fire Bolt", "Mage Hand", "Prestidigitation"],
                      "prepared": ["Magic Missile", "Shield", "Detect Magic", "Sleep", "Feather Fall"]}
    return {
        "characters": {"Ball Wizard": ball},
        "monsters": {},
        "history": [], "pinned": [], "summary": "",
        "quests": [], "npcs": {},
        "initiative": {"order": [], "turn": 0, "active": False},
        "map": {"w": 8, "h": 8, "tokens": {}},
    }


def ensure_keys(g):
    """Backfill any missing keys on a game loaded from an older save, so schema
    changes never crash the bot on existing data."""
    g.setdefault("characters", {})
    g.setdefault("monsters", {})
    g.setdefault("history", [])
    g.setdefault("pinned", [])
    g.setdefault("summary", "")
    g.setdefault("quests", [])
    g.setdefault("npcs", {})
    g.setdefault("initiative", {"order": [], "turn": 0, "active": False})
    g.setdefault("map", {"w": 8, "h": 8, "tokens": {}})
    for c in g["characters"].values():
        c.setdefault("skills", [])
        c.setdefault("spells", {"cantrips": [], "prepared": []})
        c.setdefault("dying", None)
        c.setdefault("conditions", [])
    return g
DICE_RE = re.compile(r"^\s*(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)


def roll_expr(expr):
    """Roll 'NdM+K'. Returns (total, detail_str) or (None, None)."""
    m = DICE_RE.match(expr)
    if not m:
        return None, None
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    mod = int(m.group(3).replace(" ", "")) if m.group(3) else 0
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        return None, None
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + mod
    detail = " + ".join(map(str, rolls)) + (f" {'+' if mod > 0 else '-'} {abs(mod)}" if mod else "")
    return total, detail


def resolve_term(char, term):
    """Turn 'stealth' / 'dex' / 'dexterity' into (ability, skill_label, proficient_by_skill).
    Returns None if the term isn't a known ability or skill."""
    term = term.strip().lower()
    if term in ABILS:                      # "dex"
        return term, None, False
    if term in _ABIL_FULL:                 # "dexterity"
        return _ABIL_FULL[term], None, False
    if term in SKILLS:                     # "stealth" / "sleight of hand"
        prof = any(s.lower() == term for s in char.get("skills", []))
        return SKILLS[term], term.title(), prof
    return None


def roll_check(char, term, add_prof=False):
    """Roll a d20 check for an ability or named skill. Skills auto-add proficiency
    if the character is trained. Returns (total, text, d20) or (None, None, None)."""
    resolved = resolve_term(char, term)
    if resolved is None:
        return None, None, None
    abil, skill_label, skill_prof = resolved
    proficient = add_prof or skill_prof
    mod = ability_mod(char["abilities"][abil])
    bonus = mod + (char["prof_bonus"] if proficient else 0)
    d20 = random.randint(1, 20)
    total = d20 + bonus
    label = f"{skill_label} " if skill_label else ""
    parts = f"d20({d20}) {fmt_mod(mod)} {ABILS[abil][:3]}"
    if proficient:
        parts += f" {fmt_mod(char['prof_bonus'])} prof"
    crit = "  💥 NAT 20!" if d20 == 20 else ("  💀 nat 1" if d20 == 1 else "")
    return total, f"{label}{parts} = {total}{crit}", d20


# ---- XP / leveling ----------------------------------------------------------
def apply_xp(char, delta):
    """Add XP, auto-level, return list of announcement strings."""
    char["xp"] = max(0, char["xp"] + delta)
    new_level = level_for_xp(char["xp"])
    msgs = []
    while char["level"] < new_level:
        char["level"] += 1
        gain = char["hit_die"] // 2 + 1 + ability_mod(char["abilities"]["con"])
        gain = max(1, gain)
        char["max_hp"] += gain
        char["hp"] += gain
        char["prof_bonus"] = prof_for_level(char["level"])
        msgs.append(f"⭐ **{char['class']} reaches level {char['level']}!** +{gain} max HP, proficiency now {fmt_mod(char['prof_bonus'])}.")
    return msgs


# ---- Rendering (plain text) -------------------------------------------------
def slots_str(char):
    return ", ".join(f"L{lvl}: {c}/{m}" for lvl, (c, m) in sorted(char["slots"].items())) or "none"


def render_sheet_text(name, c):
    ab = c["abilities"]
    ab_line = "  ".join(f"{k.upper()} {ab[k]}({fmt_mod(ability_mod(ab[k]))})" for k in ABILS)
    sp = c.get("spells", {})
    spell_line = ""
    if sp.get("cantrips") or sp.get("prepared"):
        spell_line = (f"\nCantrips — {', '.join(sp.get('cantrips', [])) or 'none'}"
                      f"\nSpells — {', '.join(sp.get('prepared', [])) or 'none'}")
    return (f"{name} — {c['class']} lvl {c['level']}\n"
            f"HP {c['hp']}/{c['max_hp']} | AC {c['ac']} | Prof {fmt_mod(c['prof_bonus'])} | "
            f"Gold {c['gold']} | XP {c['xp']}\n{ab_line}\n"
            f"Skills — {', '.join(c.get('skills', [])) or 'none'}\n"
            f"Spell slots — {slots_str(c)}{spell_line}\n"
            f"Conditions — {', '.join(c['conditions']) or 'none'}\n"
            f"Inventory — {', '.join(c['inventory']) or 'empty'}")


def state_for_dm(game):
    lines = []
    for name, c in game["characters"].items():
        cond = f" | conditions: {', '.join(c['conditions'])}" if c["conditions"] else ""
        lines.append(f"- {name}: {c['class']} lvl {c['level']} | HP {c['hp']}/{c['max_hp']} | AC {c['ac']} | slots {slots_str(c)}{cond}")
        if c.get("skills"):
            lines.append(f"    proficient skills: {', '.join(c['skills'])}")
        sp = c.get("spells", {})
        if sp.get("cantrips") or sp.get("prepared"):
            lines.append(f"    cantrips: {', '.join(sp.get('cantrips', [])) or 'none'}; spells: {', '.join(sp.get('prepared', [])) or 'none'}")
    for mname, mob in game.get("monsters", {}).items():
        if mob["hp"] > 0:
            lines.append(f"- [enemy] {mname}: HP {mob['hp']}/{mob['max_hp']} | AC {mob['ac']}")
    return "\n".join(lines) if lines else "(no combatants yet)"


def render_map(game):
    m = game["map"]
    w, h = m["w"], m["h"]
    grid = [["." for _ in range(w)] for _ in range(h)]
    legend = []
    for name, (x, y) in m["tokens"].items():
        if 0 <= x < w and 0 <= y < h:
            grid[y][x] = name[0].upper()
            legend.append(f"{name[0].upper()} = {name}")
    rows = ["   " + " ".join(str(x) for x in range(w))]
    for y in range(h):
        rows.append(f"{y}  " + " ".join(grid[y]))
    return "```\n" + "\n".join(rows) + "\n```\n" + ("\n".join(legend) or "(no tokens)")


def find_char(game, fragment):
    fragment = fragment.strip().lower()
    if not fragment:
        return None
    pool = list(game["characters"])
    for name in pool:
        if name.lower() == fragment:
            return name
    for name in pool:
        if name.lower().startswith(fragment):
            return name
    for name in pool:
        if fragment in name.lower():
            return name
    return None
