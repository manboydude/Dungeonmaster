"""
tests.py — standalone regression tests for the Odrun Fell bot's logic.

Run with:  python tests.py
No pytest needed. Exercises everything that can be tested without a live
Discord/Gemini connection, so a future change can't silently break the rules
engine. Prints a summary and exits non-zero if anything fails.
"""

import os
import random
import tempfile

import game
import combat
import db

_failures = []


def check(label, condition):
    status = "ok  " if condition else "FAIL"
    if not condition:
        _failures.append(label)
    print(f"  [{status}] {label}")


def test_dice_and_abilities():
    print("dice & abilities")
    random.seed(1)
    total, detail = game.roll_expr("2d6+3")
    check("2d6+3 in range 5..15", total is not None and 5 <= total <= 15)
    check("bad dice returns None", game.roll_expr("banana")[0] is None)
    check("ability_mod(15) == 2", game.ability_mod(15) == 2)
    check("ability_mod(8) == -1", game.ability_mod(8) == -1)
    check("prof scales at level 5", game.prof_for_level(5) == 3)


def test_character_build():
    print("character creation")
    f = game.build_character("Grunk", "half-orc", "fighter")
    check("fighter STR is highest+racial", f["abilities"]["str"] >= 15)
    check("fighter HP = hitdie + con mod", f["max_hp"] == 10 + game.ability_mod(f["abilities"]["con"]))
    check("fighter has skills", len(f["skills"]) == 4)
    w = game.build_character("Vex", "elf", "wizard")
    check("wizard AC is dex-based", w["ac"] == 10 + game.ability_mod(w["abilities"]["dex"]))
    check("wizard has spell slots", w["slots"] == {"1": [2, 2]})
    check("wizard knows spells", "Magic Missile" in w["spells"]["prepared"])
    check("bad race/class -> None", game.build_character("X", "klingon", "wizard") is None)


def test_skill_checks():
    print("skill checks + proficiency")
    rogue = game.build_character("Bungua", "tiefling", "rogue")
    _, text, _ = game.roll_check(rogue, "stealth")
    check("rogue auto-adds prof to stealth", "prof" in text and "Stealth" in text)
    _, text, _ = game.roll_check(rogue, "medicine")
    check("no prof on untrained skill", "prof" not in text)
    _, text, _ = game.roll_check(rogue, "dexterity")
    check("full ability name works", text is not None)
    _, text, _ = game.roll_check(rogue, "sleight of hand")
    check("multiword skill works", "prof" in text)
    check("garbage term rejected", game.roll_check(rogue, "flumph")[0] is None)


def test_leveling():
    print("xp & leveling")
    c = game.build_character("Lvl", "human", "fighter")
    start_hp = c["max_hp"]
    msgs = game.apply_xp(c, 300)
    check("300 xp -> level 2", c["level"] == 2)
    check("level up increased max HP", c["max_hp"] > start_hp)
    check("level up announced", len(msgs) == 1)
    game.apply_xp(c, 6200)
    check("~6500 xp -> level 5", c["level"] == 5 and c["prof_bonus"] == 3)


def test_death_saves():
    print("death saves")
    random.seed(2)
    c = game.build_character("Doomed", "human", "wizard"); c["hp"] = 0
    resolved = False
    for _ in range(10):
        _, resolved = game.death_save(c)
        if resolved:
            break
    check("death saves resolve", resolved)
    check("resolved as stable or dead", c["dying"] is None)


def test_combat():
    print("combat engine")
    g = game.fresh_game()
    nm, opts = combat.spawn(g, "bone-gnawer")
    check("bone-gnawer spawns", nm == "Bone-Gnawer")
    nm2, _ = combat.spawn(g, "bone-gnawer")
    check("second spawn gets unique name", nm2 == "Bone-Gnawer 2")
    check("unknown monster returns options", combat.spawn(g, "dragon")[0] is None)
    cust = combat.spawn_custom(g, "Ooze", 20, 8, 3, "1d6+1")
    check("custom spawn works", g["monsters"][cust]["hp"] == 20)
    hit, crit, dmg, txt = combat.monster_attacks(g, nm, "Ball Wizard", 12)
    check("monster attack resolves", isinstance(hit, bool) and "vs" in txt)


def test_db_roundtrip():
    print("sqlite persistence")
    path = os.path.join(tempfile.gettempdir(), "dnd_test_%d.db" % random.randint(0, 999999))
    db.init(path)
    g = game.fresh_game(); g["characters"]["Ball Wizard"]["gold"] = 77
    db.save_game(123, g)
    loaded = db.load_game(123, game.fresh_game)
    check("gold persisted", loaded["characters"]["Ball Wizard"]["gold"] == 77)
    fresh = db.load_game(999, game.fresh_game)
    check("missing channel uses factory", fresh["characters"]["Ball Wizard"]["gold"] == 0)
    os.remove(path)


def test_ensure_keys():
    print("schema backfill")
    old = {"characters": {"Bob": {"class": "Fighter", "level": 1, "ac": 12, "hp": 8,
                                   "max_hp": 8, "hit_die": 10, "abilities": {k: 10 for k in game.ABILS},
                                   "prof_bonus": 2, "slots": {}, "gold": 0, "xp": 0, "inventory": []}}}
    g = game.ensure_keys(old)
    check("quests key backfilled", "quests" in g and g["quests"] == [])
    check("npcs key backfilled", "npcs" in g)
    check("character skills backfilled", g["characters"]["Bob"]["skills"] == [])
    check("character spells backfilled", "cantrips" in g["characters"]["Bob"]["spells"])


def main():
    for t in (test_dice_and_abilities, test_character_build, test_skill_checks,
              test_leveling, test_death_saves, test_combat, test_db_roundtrip, test_ensure_keys):
        t()
    print()
    if _failures:
        print(f"❌ {len(_failures)} test(s) FAILED: {', '.join(_failures)}")
        raise SystemExit(1)
    print("✅ all tests passed")


if __name__ == "__main__":
    main()
