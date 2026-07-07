"""
combat.py — a real combat engine. Monster stat blocks (SRD open content),
spawning, and attack resolution the bot runs automatically.
"""

import random
from game import roll_expr, ability_mod, fmt_mod

# SRD monsters — open game content, safe to include.
MONSTERS = {
    "goblin":       {"ac": 15, "hp": 7,  "hit_die": "2d6",  "attack": ("Scimitar", 4, "1d6+2")},
    "wolf":         {"ac": 13, "hp": 11, "hit_die": "2d8+2", "attack": ("Bite", 4, "2d4+2")},
    "skeleton":     {"ac": 13, "hp": 13, "hit_die": "2d8+4", "attack": ("Shortsword", 4, "1d6+2")},
    "bandit":       {"ac": 12, "hp": 11, "hit_die": "2d8+2", "attack": ("Scimitar", 3, "1d6+1")},
    "zombie":       {"ac": 8,  "hp": 22, "hit_die": "3d8+9", "attack": ("Slam", 3, "1d6+1")},
    "giant spider": {"ac": 14, "hp": 26, "hit_die": "4d10+4", "attack": ("Bite", 5, "1d8+3")},
    "cultist":      {"ac": 12, "hp": 9,  "hit_die": "2d8",  "attack": ("Ritual dagger", 3, "1d4+1")},
    # Odrun Fell flavor creatures:
    "bone-gnawer":  {"ac": 12, "hp": 5,  "hit_die": "2d4",  "attack": ("Bite", 3, "1d4+1")},
    "ichor-hound":  {"ac": 13, "hp": 14, "hit_die": "3d8+3", "attack": ("Claw", 4, "1d6+2")},
    "relic-wight":  {"ac": 14, "hp": 22, "hit_die": "4d8+4", "attack": ("Bone blade", 4, "1d8+2")},
}


def spawn_custom(game, label, hp, ac, to_hit, dmg_expr):
    """Stat a made-up creature on the fly, e.g. a monster the DM invented."""
    name, n = label, 1
    while name in game["monsters"]:
        n += 1
        name = f"{label} {n}"
    game["monsters"][name] = {
        "type": "custom", "ac": ac, "hp": hp, "max_hp": hp,
        "attack": ["Attack", to_hit, dmg_expr],
    }
    return name


def spawn(game, mtype, custom_name=None):
    mtype = mtype.strip().lower()
    if mtype not in MONSTERS:
        return None, sorted(MONSTERS.keys())
    base = MONSTERS[mtype]
    # unique instance name: "Goblin", "Goblin 2", ...
    label = custom_name or mtype.title()
    name, n = label, 1
    while name in game["monsters"]:
        n += 1
        name = f"{label} {n}"
    game["monsters"][name] = {
        "type": mtype, "ac": base["ac"], "hp": base["hp"], "max_hp": base["hp"],
        "attack": list(base["attack"]),
    }
    return name, None


def resolve_attack(attacker_name, to_hit, dmg_expr, atk_name, target_name, target_ac):
    """Roll a single attack. Returns (hit, crit, damage, text)."""
    d20 = random.randint(1, 20)
    crit = d20 == 20
    total = d20 + to_hit
    hit = crit or (d20 != 1 and total >= target_ac)
    header = f"⚔️ **{attacker_name}** {atk_name} vs **{target_name}** (AC {target_ac}): d20({d20}) {fmt_mod(to_hit)} = **{total}**"
    if not hit:
        return False, False, 0, header + " — **miss**."
    dmg, detail = roll_expr(dmg_expr)
    if crit:  # crude crit: add the dice max isn't tracked; re-roll dice portion and add
        extra, _ = roll_expr(dmg_expr.split("+")[0] if "+" in dmg_expr else dmg_expr)
        dmg = (dmg or 0) + (extra or 0)
        header += "  💥 CRIT!"
    return True, crit, dmg or 0, header + f" — **hit for {dmg}** ({detail})."


def monster_attacks(game, monster_name, target_name, target_ac):
    mob = game["monsters"].get(monster_name)
    if not mob or mob["hp"] <= 0:
        return None
    atk_name, to_hit, dmg_expr = mob["attack"]
    return resolve_attack(monster_name, to_hit, dmg_expr, atk_name, target_name, target_ac)


def pc_basic_attack(char, char_name, target_name, target_ac, finesse=False):
    """A simple weapon swing for a PC: d20 + (STR or DEX) + prof, 1d8 + mod."""
    ab = "dex" if finesse else "str"
    mod = ability_mod(char["abilities"][ab])
    to_hit = mod + char["prof_bonus"]
    return resolve_attack(char_name, to_hit, f"1d8{fmt_mod(mod)}", "attack", target_name, target_ac)


def living_monsters(game):
    return {n: m for n, m in game["monsters"].items() if m["hp"] > 0}
