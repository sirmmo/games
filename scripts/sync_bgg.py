#!/usr/bin/env python3
"""Sync a BoardGameGeek collection into static JSON + locally cached box art.

BGG closed the XML API to anonymous requests in October 2025, so every call
carries `Authorization: Bearer $BGG_TOKEN`. Register one at
https://boardgamegeek.com/using_the_xml_api

Without a token, use import_csv.py instead — it works off a CSV exported from
the website and needs no credentials.
"""

import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bggshelf as bs  # noqa: E402

API = "https://boardgamegeek.com/xmlapi2"
USERNAME = os.environ.get("BGG_USERNAME", "sirmmo")
TOKEN = os.environ.get("BGG_TOKEN", "").strip()

THING_BATCH = 20
POLITE_DELAY = 2.0


def fetch(path, params, attempt_budget=8):
    """GET an XML API endpoint, honouring BGG's 202-queued and 429 responses."""
    url = "{}/{}?{}".format(API, path, urllib.parse.urlencode(params))
    headers = {"User-Agent": bs.USER_AGENT, "Accept": "text/xml"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN

    delay = 3.0
    for _ in range(attempt_budget):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                if resp.status == 202:      # collection queued server-side
                    bs.log("  202 queued, retrying in {:.0f}s".format(delay))
                    time.sleep(delay)
                    delay = min(delay * 1.6, 45)
                    continue
                return ET.fromstring(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SystemExit(
                    "BGG returned 401 Unauthorized.\n"
                    "The XML API has required a registered token since Oct 2025.\n"
                    "Register at https://boardgamegeek.com/using_the_xml_api and set\n"
                    "the BGG_TOKEN secret (or env var). Token currently "
                    + ("set but rejected." if TOKEN else "NOT set.")
                    + "\nNo token? Run scripts/import_csv.py against a CSV export instead."
                )
            if e.code in (202, 429, 500, 502, 503, 504):
                bs.log("  HTTP {}, retrying in {:.0f}s".format(e.code, delay))
            else:
                raise
        except (urllib.error.URLError, ET.ParseError) as e:
            bs.log("  {}, retrying in {:.0f}s".format(type(e).__name__, delay))
        time.sleep(delay)
        delay = min(delay * 1.6, 45)
    raise SystemExit("Gave up on {} after {} attempts".format(url, attempt_budget))


def attr(el, name, cast=float, default=None, zero_is_none=False):
    if el is None:
        return default
    raw = el.get(name)
    if raw in (None, ""):
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        return default
    return default if zero_is_none and value == 0 else value


def text_int(el, default=None):
    if el is None or not (el.text or "").strip():
        return default
    try:
        return int(el.text.strip())
    except ValueError:
        return default


def fetch_collection(subtype, exclude=None):
    params = {"username": USERNAME, "stats": 1, "own": 1, "subtype": subtype}
    if exclude:
        params["excludesubtype"] = exclude
    bs.log("Fetching collection: subtype={}{}".format(
        subtype, " excluding " + exclude if exclude else ""))

    items = {}
    for item in fetch("collection", params).findall("item"):
        oid = item.get("objectid")
        if not oid:
            continue
        stats = item.find("stats")
        rating_el = stats.find("rating") if stats is not None else None
        items[oid] = {
            "id": int(oid),
            "name": (item.findtext("name") or "").strip(),
            "year": text_int(item.find("yearpublished")),
            "image": (item.findtext("image") or "").strip(),
            "plays": text_int(item.find("numplays"), 0) or 0,
            "comment": bs.clean(item.findtext("comment")),
            "myRating": attr(rating_el, "value", float),
            "isExpansion": subtype == "boardgameexpansion",
            "url": "https://boardgamegeek.com/boardgame/{}".format(oid),
        }
    bs.log("  {} items".format(len(items)))
    return items


def player_polls(item):
    """The suggested_numplayers poll: which counts the community calls Best."""
    best, recommended = [], []
    for poll in item.findall("poll"):
        if poll.get("name") != "suggested_numplayers":
            continue
        for results in poll.findall("results"):
            votes = {r.get("value"): int(r.get("numvotes") or 0) for r in results.findall("result")}
            if not votes or sum(votes.values()) == 0:
                continue
            winner = max(votes, key=lambda k: votes[k])
            if winner == "Best":
                best.append(results.get("numplayers"))
            elif winner == "Recommended":
                recommended.append(results.get("numplayers"))
    return best, recommended


def fetch_things(ids):
    out = {}
    for i in range(0, len(ids), THING_BATCH):
        chunk = ids[i:i + THING_BATCH]
        bs.log("Fetching details {}-{} of {}".format(i + 1, i + len(chunk), len(ids)))
        for item in fetch("thing", {"id": ",".join(chunk), "stats": 1}).findall("item"):
            links, expands, expansion_ids = {}, [], []
            for link in item.findall("link"):
                ltype = link.get("type", "")
                if ltype == "boardgameexpansion":
                    if link.get("inbound") == "true":
                        expands.append({"id": int(link.get("id")), "name": link.get("value")})
                    else:
                        expansion_ids.append(int(link.get("id")))
                    continue
                links.setdefault(ltype, []).append(link.get("value"))

            ratings = item.find("statistics/ratings")
            rank = None
            if ratings is not None:
                for r in ratings.findall("ranks/rank"):
                    if r.get("name") == "boardgame":
                        rank = attr(r, "value", int)
                        break

            best, recommended = player_polls(item)
            out[item.get("id")] = {
                "description": bs.truncate(bs.clean(item.findtext("description")), 1200),
                "minPlayers": attr(item.find("minplayers"), "value", int, zero_is_none=True),
                "maxPlayers": attr(item.find("maxplayers"), "value", int, zero_is_none=True),
                "minPlaytime": attr(item.find("minplaytime"), "value", int, zero_is_none=True),
                "maxPlaytime": attr(item.find("maxplaytime"), "value", int, zero_is_none=True),
                "playingTime": attr(item.find("playingtime"), "value", int, zero_is_none=True),
                "bestWith": best,
                "recommendedWith": recommended,
                "rating": attr(ratings.find("average") if ratings is not None else None, "value", float),
                "weight": attr(ratings.find("averageweight") if ratings is not None else None, "value", float),
                "rank": rank,
                "designers": links.get("boardgamedesigner", [])[:6],
                "artists": links.get("boardgameartist", [])[:6],
                "publishers": links.get("boardgamepublisher", [])[:3],
                "categories": links.get("boardgamecategory", []),
                "mechanics": links.get("boardgamemechanic", []),
                "expands": expands,
                "expansionIds": expansion_ids,
            }
        time.sleep(POLITE_DELAY)
    return out


def main():
    if not TOKEN:
        bs.log("WARNING: BGG_TOKEN is not set — the XML API will reject this run.")

    games = fetch_collection("boardgame", exclude="boardgameexpansion")
    time.sleep(POLITE_DELAY)
    merged = dict(games)
    merged.update(fetch_collection("boardgameexpansion"))
    if not merged:
        raise SystemExit("Collection came back empty — refusing to overwrite the shelf.")

    details = fetch_things(sorted(merged.keys(), key=int))
    for oid, game in merged.items():
        game.update(details.get(oid, {}))

    owned = set(int(o) for o in merged)
    out = []
    for game in merged.values():
        if not game["isExpansion"]:
            game["ownedExpansions"] = sum(1 for e in game.get("expansionIds") or [] if e in owned)
        game.pop("expansionIds", None)
        out.append(game)

    if "--no-covers" not in sys.argv:
        for game in out:
            info = bs.cover_for(game["id"], game.get("image"), game["name"])
            if info:
                game.update(info)
        bs.prune_covers(set(str(g["id"]) for g in out))

    bs.write_collection(out, USERNAME, "xmlapi")


if __name__ == "__main__":
    main()
