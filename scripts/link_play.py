#!/usr/bin/env python3
"""Attach each shelved game to the places it can actually be played.

Two counterparts, both matched on BoardGameGeek id rather than on titles —
this collection is largely in Italian, so name matching would be hopeless:

  Board Game Arena  its public game list embeds `bgg_id` for every game, which
                    makes the mapping authoritative rather than guessed.
  JustPlay          each pack declares the `bgg` id of the game it implements
                    (see the launcher's packs/SCHEMA.md).

Both sources are snapshotted into site/data/, so a sync still works — and the
site still links — when either is unreachable.

Usage:  python scripts/link_play.py [--offline]
"""

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data"
COLLECTION = DATA / "collection.json"
BGA_SNAPSHOT = DATA / "bga-games.json"
JP_SNAPSHOT = DATA / "justplay-packs.json"

BGA_LIST = "https://boardgamearena.com/gamelist"
BGA_PANEL = "https://boardgamearena.com/gamepanel?game={}"
JP_BASE = "https://launcher.justplaybo.it/"
JP_INDEX = JP_BASE + "packs/index.json"
JP_PLAY = JP_BASE + "?game={}"

UA = "sirmmo-games-kallax/1.0 (+https://github.com/sirmmo/games)"


def log(msg):
    print(msg, flush=True)


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def load_snapshot(path):
    try:
        return json.loads(path.read_text())
    except (IOError, OSError, ValueError):
        return None


# ------------------------------------------------------------------ BGA ----

def scrape_bga(html):
    """Pull {slug, bgg, title, status} out of the game list page.

    The list is embedded as JSON in the page. Rather than guess at the wrapping
    variable, walk out from each `"bgg_id"` to its enclosing object and parse
    that — which survives the surrounding markup changing.
    """
    games = {}
    for m in re.finditer(r'"bgg_id"', html):
        i = m.start()
        depth, j = 0, i
        while j > 0:
            if html[j] == '}':
                depth += 1
            elif html[j] == '{':
                if depth == 0:
                    break
                depth -= 1
            j -= 1
        depth, k = 0, j
        while k < len(html):
            if html[k] == '{':
                depth += 1
            elif html[k] == '}':
                depth -= 1
                if depth == 0:
                    break
            k += 1
        try:
            o = json.loads(html[j:k + 1])
        except ValueError:
            continue
        slug, bgg = o.get("name"), o.get("bgg_id")
        try:
            bgg = int(bgg)
        except (TypeError, ValueError):
            continue
        if not slug or bgg <= 0:
            continue
        games[slug] = {
            "slug": slug,
            "bgg": bgg,
            "title": o.get("display_name_en") or slug,
            "status": o.get("status") or "public",
        }
    return sorted(games.values(), key=lambda g: g["slug"])


def fetch_bga(offline):
    if not offline:
        try:
            games = scrape_bga(get(BGA_LIST))
            if len(games) > 200:                      # sanity: the catalogue is ~1300
                BGA_SNAPSHOT.write_text(json.dumps(games, indent=1, sort_keys=True))
                log("Board Game Arena: {} games (snapshot refreshed)".format(len(games)))
                return games
            log("Board Game Arena: only {} games parsed — keeping the snapshot".format(len(games)))
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            log("Board Game Arena unreachable ({}) — falling back to the snapshot".format(e))
    games = load_snapshot(BGA_SNAPSHOT) or []
    log("Board Game Arena: {} games (from snapshot)".format(len(games)))
    return games


# ------------------------------------------------------------- JustPlay ----

def fetch_justplay(offline):
    if not offline:
        try:
            index = json.loads(get(JP_INDEX, timeout=30))
            packs = []
            for entry in index.get("packs", []):
                ref = entry.get("ref")
                if not ref:
                    continue
                try:
                    pack = json.loads(get(JP_BASE + ref, timeout=30))
                except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
                    log("  ! {} unreadable: {}".format(ref, e))
                    continue
                bgg = pack.get("bgg")
                try:
                    bgg = int(bgg)
                except (TypeError, ValueError):
                    continue                          # not one published game
                packs.append({
                    "id": pack.get("id") or entry.get("id"),
                    "name": pack.get("name") or entry.get("name"),
                    "ref": ref,
                    "bgg": bgg,
                })
            if packs:
                JP_SNAPSHOT.write_text(json.dumps(packs, indent=1, sort_keys=True))
                log("JustPlay: {} packs with a BGG id (snapshot refreshed)".format(len(packs)))
                return packs
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            log("JustPlay unreachable ({}) — falling back to the snapshot".format(e))
    packs = load_snapshot(JP_SNAPSHOT) or []
    log("JustPlay: {} packs (from snapshot)".format(len(packs)))
    return packs


# ----------------------------------------------------------------- link ----

def main():
    offline = "--offline" in sys.argv
    DATA.mkdir(parents=True, exist_ok=True)

    data = json.loads(COLLECTION.read_text())
    games = data.get("games") or []
    if not games:
        raise SystemExit("Collection is empty — run an importer first.")

    bga_by_bgg = {}
    for g in fetch_bga(offline):
        bga_by_bgg.setdefault(g["bgg"], g)            # first slug wins, list is sorted

    jp_by_bgg = {}
    for p in fetch_justplay(offline):
        jp_by_bgg.setdefault(p["bgg"], []).append(p)

    linked_bga = linked_jp = 0
    for game in games:
        play = {}
        hit = bga_by_bgg.get(game["id"])
        if hit:
            play["bga"] = {
                "slug": hit["slug"],
                "title": hit["title"],
                "url": BGA_PANEL.format(hit["slug"]),
                "beta": hit.get("status") == "beta",
            }
            linked_bga += 1
        packs = jp_by_bgg.get(game["id"]) or []
        if packs:
            play["justplay"] = [{
                "id": p["id"],
                "name": p["name"],
                "url": JP_PLAY.format(p["ref"]),
            } for p in sorted(packs, key=lambda p: p["id"])]
            linked_jp += 1
        if play:
            game["play"] = play
        else:
            game.pop("play", None)                    # a game may leave a catalogue

    data["counts"]["onBga"] = linked_bga
    data["counts"]["onJustPlay"] = linked_jp
    COLLECTION.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
    log("Linked {} games to Board Game Arena and {} to JustPlay".format(linked_bga, linked_jp))


if __name__ == "__main__":
    main()
