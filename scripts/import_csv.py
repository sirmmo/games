#!/usr/bin/env python3
"""Build the shelf from a collection CSV exported from the BGG website.

The CSV carries almost everything: ratings, plays, rank, weight, player counts,
play time and BGG's own best/recommended player poll results. What it lacks is
box art and credits, which come from api.geekdo.com — the JSON API the website
itself uses, which needs no token, unlike the XML API.

Usage:
    python scripts/import_csv.py [collection.csv] [--no-covers] [--limit N]

Export a fresh CSV from https://boardgamegeek.com/collection/user/<you>
via "Download as CSV".
"""

import collections
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bggshelf as bs  # noqa: E402

GEEKITEM = "https://api.geekdo.com/api/geekitems?objectid={}&objecttype=thing&subtype=boardgame"
POLITE_DELAY = 0.8      # seconds between geekitems calls
MAX_EXPANSION_LINKS = 80


def merge_copy(base, extra):
    """Fold a second collection entry for the same game into the first.

    Owning two copies is one game on the shelf, not two. Note that BGG repeats
    the game-level play count on every copy, so those must not be added up.
    """
    base["copies"] = base.get("copies", 1) + 1
    base["plays"] = max(base.get("plays") or 0, extra.get("plays") or 0)
    if not base.get("myRating"):
        base["myRating"] = extra.get("myRating")
    for key, sep in (("edition", "; "), ("comment", " · ")):
        parts = [p for p in (base.get(key), extra.get(key)) if p]
        seen = list(dict.fromkeys(parts))
        base[key] = sep.join(seen) if seen else base.get(key)


def parse_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    by_id = collections.OrderedDict()
    for r in rows:
        if r.get("own") != "1":
            continue
        oid = (r.get("objectid") or "").strip()
        if not oid:
            continue
        record = {
            "id": int(oid),
            "name": (r.get("objectname") or "").strip(),
            "originalName": (r.get("originalname") or "").strip() or None,
            "year": bs.as_int(r.get("yearpublished")),
            "isExpansion": r.get("itemtype") == "expansion",
            "myRating": bs.as_float(r.get("rating")),
            "rating": bs.as_float(r.get("average")),
            "weight": bs.as_float(r.get("avgweight")),
            "rank": bs.as_int(r.get("rank")),
            "plays": bs.as_int(r.get("numplays"), default=0, zero_is_none=False),
            "minPlayers": bs.as_int(r.get("minplayers")),
            "maxPlayers": bs.as_int(r.get("maxplayers")),
            "playingTime": bs.as_int(r.get("playingtime")),
            "minPlaytime": bs.as_int(r.get("minplaytime")),
            "maxPlaytime": bs.as_int(r.get("maxplaytime")),
            "bestWith": split_counts(r.get("bggbestplayers")),
            "recommendedWith": split_counts(r.get("bggrecplayers")),
            "comment": (r.get("comment") or "").strip(),
            "edition": edition_of(r),
            "url": "https://boardgamegeek.com/boardgame/{}".format(oid),
        }
        if oid in by_id:
            merge_copy(by_id[oid], record)
        else:
            record["copies"] = 1
            by_id[oid] = record

    return list(by_id.values())


def split_counts(value):
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def edition_of(row):
    """A one-line note about which physical copy is on the shelf."""
    bits = [
        (row.get("version_nickname") or "").strip(),
        (row.get("version_languages") or "").strip(),
        (row.get("version_publishers") or "").strip(),
    ]
    bits = [b for b in bits if b]
    return " · ".join(dict.fromkeys(bits)) or None


def names_from(links, key, limit=None):
    items = links.get(key) or []
    out = [i.get("name") for i in items if isinstance(i, dict) and i.get("name")]
    return out[:limit] if limit else out


def fetch_details(game):
    """Art, credits and blurb for one game, from the tokenless JSON API."""
    raw = bs.http_bytes(GEEKITEM.format(game["id"]), timeout=45)
    item = (json.loads(raw.decode("utf-8")) or {}).get("item") or {}
    links = item.get("links") or {}

    expansion_ids = []
    for link in (links.get("boardgameexpansion") or [])[:MAX_EXPANSION_LINKS]:
        try:
            expansion_ids.append(int(link.get("objectid")))
        except (TypeError, ValueError):
            continue

    return {
        # imageurl@2x is the signed 492x600 variant — already the right size.
        "image": item.get("imageurl@2x") or item.get("imageurl") or "",
        "description": bs.truncate(bs.clean(item.get("description")), 1200),
        "designers": names_from(links, "boardgamedesigner", 6),
        "artists": names_from(links, "boardgameartist", 6),
        "publishers": names_from(links, "boardgamepublisher", 3),
        "categories": names_from(links, "boardgamecategory"),
        "mechanics": names_from(links, "boardgamemechanic"),
        "expansionIds": expansion_ids,
    }


ENRICHED_KEYS = ("image", "description", "designers", "artists", "publishers",
                 "categories", "mechanics", "expansionIds")

# 566 items is a long enough fetch that losing it to an interruption hurts.
CACHE_FILE = bs.DATA / ".details-cache.json"
CACHE_EVERY = 20


def load_detail_cache():
    """Previous output plus any partial progress from an interrupted run."""
    cache = {}
    for gid, game in bs.load_existing().items():
        if game.get("image") and "mechanics" in game:
            cache[gid] = {k: game[k] for k in ENRICHED_KEYS if k in game}
    try:
        cache.update(json.loads(CACHE_FILE.read_text()))
    except (IOError, OSError, ValueError):
        pass
    return cache


def save_detail_cache(cache):
    bs.DATA.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache))


def main():
    argv = sys.argv[1:]
    want_covers = "--no-covers" not in argv
    limit = None
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--limit":
            limit = int(argv[i + 1]); i += 2; continue
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
        elif not a.startswith("--"):
            positional.append(a)
        i += 1
    csv_path = Path(positional[0]) if positional else bs.ROOT / "collection.csv"

    if not csv_path.exists():
        raise SystemExit("No CSV at {}".format(csv_path))

    games = parse_csv(csv_path)
    if limit:
        games = games[:limit]
    if not games:
        raise SystemExit("CSV had no owned rows — refusing to overwrite the shelf.")
    bs.log("Read {} owned items from {}".format(len(games), csv_path.name))

    cache = load_detail_cache()
    bs.log("Detail cache holds {} items".format(len(cache)))
    fetched = 0
    for i, g in enumerate(games, 1):
        cached = cache.get(str(g["id"]))
        if cached:
            g.update(cached)
            continue
        try:
            detail = fetch_details(g)
            g.update(detail)
            cache[str(g["id"])] = detail
            fetched += 1
        except Exception as e:
            bs.log("  ! details failed for {} ({}): {}".format(g["name"], g["id"], e))
            g.setdefault("image", "")
        if fetched and fetched % CACHE_EVERY == 0:
            save_detail_cache(cache)
            bs.log("  fetched details for {} new items ({}/{})".format(fetched, i, len(games)))
        time.sleep(POLITE_DELAY)
    save_detail_cache(cache)
    bs.log("Details: {} fetched, {} reused from cache".format(fetched, len(games) - fetched))

    # Expansion links only run base -> expansion, so invert them to tell each
    # owned expansion which owned game it belongs to.
    owned = {g["id"]: g for g in games}
    parent = {}
    for g in games:
        for eid in g.get("expansionIds") or []:
            if eid in owned:
                parent.setdefault(eid, {"id": g["id"], "name": g["name"]})
    for g in games:
        g["expands"] = [parent[g["id"]]] if g["isExpansion"] and g["id"] in parent else []
        if not g["isExpansion"]:
            g["ownedExpansions"] = sum(1 for eid in (g.get("expansionIds") or []) if eid in owned)
        g.pop("expansionIds", None)

    if want_covers:
        done = 0
        for g in games:
            info = bs.cover_for(g["id"], g.get("image"), g["name"])
            if info:
                g.update(info)
                done += 1
                if done % 50 == 0:
                    bs.log("  covers: {}/{}".format(done, len(games)))
        bs.log("Covers ready for {}/{} items".format(done, len(games)))
        bs.prune_covers({str(g["id"]) for g in games})

    linked = sum(1 for g in games if g.get("expands"))
    bs.log("Linked {} expansions to an owned base game".format(linked))
    bs.write_collection(games, "sirmmo", "csv")


if __name__ == "__main__":
    main()
