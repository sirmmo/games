#!/usr/bin/env python3
"""Sync a BoardGameGeek collection into static JSON + locally cached box art.

BGG locked the XML API behind registered bearer tokens in October 2025, so every
request carries `Authorization: Bearer $BGG_TOKEN`. Register one at
https://boardgamegeek.com/using_the_xml_api

Covers are downloaded once and resized locally: the CDN signs the resize path of
its image URLs, so a smaller variant cannot be synthesised from the original URL.
"""

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

API = "https://boardgamegeek.com/xmlapi2"
USER_AGENT = "sirmmo-games-kallax/1.0 (+https://github.com/sirmmo/games)"

USERNAME = os.environ.get("BGG_USERNAME", "sirmmo")
TOKEN = os.environ.get("BGG_TOKEN", "").strip()
ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
COVERS = SITE / "covers"
DATA = SITE / "data"

COVER_HEIGHT = 600          # px, enough for a retina cubby
COVER_QUALITY = 82
THING_BATCH = 20            # ids per /thing request
POLITE_DELAY = 2.0          # seconds between API calls


def log(msg):
    print(msg, flush=True)


def fetch(path, params, attempt_budget=8):
    """GET an XML API endpoint, honouring BGG's 202-queued and 429 responses."""
    qs = urllib.parse.urlencode(params)
    url = "{}/{}?{}".format(API, path, qs)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/xml"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN

    delay = 3.0
    for attempt in range(1, attempt_budget + 1):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read()
                if resp.status == 202:
                    # Collection request queued server-side; come back for it.
                    log("  202 queued, retrying in {:.0f}s".format(delay))
                    time.sleep(delay)
                    delay = min(delay * 1.6, 45)
                    continue
                return ET.fromstring(body)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SystemExit(
                    "BGG returned 401 Unauthorized.\n"
                    "The XML API requires a registered token since Oct 2025.\n"
                    "Register at https://boardgamegeek.com/using_the_xml_api and set\n"
                    "the BGG_TOKEN secret (or env var). Token currently "
                    + ("set but rejected." if TOKEN else "NOT set.")
                )
            if e.code in (202, 429, 500, 502, 503, 504):
                log("  HTTP {}, retrying in {:.0f}s".format(e.code, delay))
                time.sleep(delay)
                delay = min(delay * 1.6, 45)
                continue
            raise
        except (urllib.error.URLError, ET.ParseError) as e:
            log("  {}, retrying in {:.0f}s".format(type(e).__name__, delay))
            time.sleep(delay)
            delay = min(delay * 1.6, 45)
    raise SystemExit("Gave up on {} after {} attempts".format(url, attempt_budget))


def attr_num(el, name, cast=float, default=None):
    if el is None:
        return default
    raw = el.get(name)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def text_num(el, cast=int, default=None):
    if el is None or not (el.text or "").strip():
        return default
    try:
        return cast(el.text.strip())
    except (TypeError, ValueError):
        return default


def clean(text):
    """Flatten a BGG description to plain text.

    BGG double-encodes these: ElementTree decodes one layer, leaving literal
    entities like `&#10;` behind, so a second unescape pass is needed.
    """
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:") + "\u2026"


def fetch_collection(subtype, exclude=None):
    params = {
        "username": USERNAME,
        "stats": 1,
        "own": 1,
        "subtype": subtype,
    }
    if exclude:
        params["excludesubtype"] = exclude
    log("Fetching collection: subtype={}{}".format(subtype, " excluding " + exclude if exclude else ""))
    root = fetch("collection", params)
    items = {}
    for item in root.findall("item"):
        oid = item.get("objectid")
        if not oid:
            continue
        stats = item.find("stats")
        rating_el = stats.find("rating") if stats is not None else None
        my_rating = attr_num(rating_el, "value", float) if rating_el is not None else None
        items[oid] = {
            "id": int(oid),
            "name": (item.findtext("name") or "").strip(),
            "year": text_num(item.find("yearpublished")),
            "image": (item.findtext("image") or "").strip(),
            "thumb": (item.findtext("thumbnail") or "").strip(),
            "plays": text_num(item.find("numplays"), int, 0) or 0,
            "comment": clean(item.findtext("comment")),
            "myRating": my_rating,
            "isExpansion": subtype == "boardgameexpansion",
        }
    log("  {} items".format(len(items)))
    return items


def best_player_counts(item):
    """Read the suggested_numplayers poll: counts the community calls Best."""
    best, recommended = [], []
    for poll in item.findall("poll"):
        if poll.get("name") != "suggested_numplayers":
            continue
        for results in poll.findall("results"):
            n = results.get("numplayers")
            votes = {r.get("value"): int(r.get("numvotes") or 0) for r in results.findall("result")}
            if not votes or sum(votes.values()) == 0:
                continue
            winner = max(votes, key=lambda k: votes[k])
            if winner == "Best":
                best.append(n)
            elif winner == "Recommended":
                recommended.append(n)
    return best, recommended


def fetch_things(ids):
    """Enrich collection items with designers, weight, rank, mechanics, poll."""
    out = {}
    for i in range(0, len(ids), THING_BATCH):
        chunk = ids[i:i + THING_BATCH]
        log("Fetching details {}-{} of {}".format(i + 1, i + len(chunk), len(ids)))
        root = fetch("thing", {"id": ",".join(chunk), "stats": 1})
        for item in root.findall("item"):
            oid = item.get("id")
            links = {}
            expands = []
            for link in item.findall("link"):
                ltype = link.get("type", "")
                if ltype == "boardgameexpansion" and link.get("inbound") == "true":
                    expands.append({"id": int(link.get("id")), "name": link.get("value")})
                    continue
                links.setdefault(ltype, []).append(link.get("value"))

            ratings = item.find("statistics/ratings")
            rank = None
            if ratings is not None:
                for r in ratings.findall("ranks/rank"):
                    if r.get("name") == "boardgame":
                        rank = attr_num(r, "value", int)
                        break

            best, recommended = best_player_counts(item)
            out[oid] = {
                "description": truncate(clean(item.findtext("description")), 1200),
                "minPlayers": attr_num(item.find("minplayers"), "value", int),
                "maxPlayers": attr_num(item.find("maxplayers"), "value", int),
                "minPlaytime": attr_num(item.find("minplaytime"), "value", int),
                "maxPlaytime": attr_num(item.find("maxplaytime"), "value", int),
                "playingTime": attr_num(item.find("playingtime"), "value", int),
                "minAge": attr_num(item.find("minage"), "value", int),
                "bestWith": best,
                "recommendedWith": recommended,
                "rating": attr_num(ratings.find("average") if ratings is not None else None, "value", float),
                "weight": attr_num(ratings.find("averageweight") if ratings is not None else None, "value", float),
                "rank": rank,
                "designers": links.get("boardgamedesigner", [])[:6],
                "artists": links.get("boardgameartist", [])[:6],
                "publishers": links.get("boardgamepublisher", [])[:3],
                "categories": links.get("boardgamecategory", []),
                "mechanics": links.get("boardgamemechanic", []),
                "families": links.get("boardgamefamily", [])[:8],
                "expands": expands,
            }
        time.sleep(POLITE_DELAY)
    return out


def shelf_tint(rgb):
    """Turn a box's dominant colour into a shadowed cubby interior.

    Desaturated and normalised to a constant luminance so every recess reads as
    the same depth regardless of how bright the box is, and so the value can be
    used as a plain hex colour rather than a color-mix() the browser may not
    support.
    """
    r, g, b = [c / 255.0 for c in rgb]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r, g, b = [luma + (c - luma) * 0.55 for c in (r, g, b)]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    scale = min(0.20 / max(luma, 0.02), 6.0)
    out = [min(255, max(0, int(round(c * scale * 255)))) for c in (r, g, b)]
    return "#{:02x}{:02x}{:02x}".format(*out)


def cover_for(game):
    """Download + downscale box art once; reuse the cached file forever after."""
    from PIL import Image  # imported late so --no-covers works without Pillow

    gid = game["id"]
    dest = COVERS / "{}.webp".format(gid)
    meta = COVERS / "{}.json".format(gid)
    if dest.exists() and meta.exists():
        try:
            return json.loads(meta.read_text())
        except ValueError:
            pass

    src = game.get("image") or game.get("thumb")
    if not src:
        return None

    tmp = COVERS / "{}.tmp".format(gid)
    req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            tmp.write_bytes(resp.read())
    except Exception as e:
        log("  ! cover download failed for {} ({}): {}".format(game["name"], gid, e))
        return None

    try:
        with Image.open(tmp) as im:
            im = im.convert("RGBA")
            flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
            flat.alpha_composite(im)
            rgb = flat.convert("RGB")

            w, h = rgb.size
            if h > COVER_HEIGHT:
                rgb = rgb.resize((max(1, round(w * COVER_HEIGHT / h)), COVER_HEIGHT), Image.LANCZOS)

            # 1px downscale gives the dominant colour used to tint the cubby.
            tint = rgb.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
            rgb.save(dest, "WEBP", quality=COVER_QUALITY, method=6)
            info = {
                "cover": "covers/{}.webp".format(gid),
                "w": rgb.size[0],
                "h": rgb.size[1],
                "tint": shelf_tint(tint),
            }
            meta.write_text(json.dumps(info))
            return info
    except Exception as e:
        log("  ! cover processing failed for {} ({}): {}".format(game["name"], gid, e))
        return None
    finally:
        if tmp.exists():
            tmp.unlink()


def prune_covers(keep_ids):
    removed = 0
    for f in COVERS.glob("*"):
        if f.suffix not in (".webp", ".json"):
            continue
        if f.stem not in keep_ids:
            f.unlink()
            removed += 1
    if removed:
        log("Pruned {} orphaned cover files".format(removed))


def main():
    if not TOKEN:
        log("WARNING: BGG_TOKEN is not set - the XML API will reject this run.")

    COVERS.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)

    games = fetch_collection("boardgame", exclude="boardgameexpansion")
    time.sleep(POLITE_DELAY)
    expansions = fetch_collection("boardgameexpansion")

    merged = dict(games)
    merged.update(expansions)
    if not merged:
        raise SystemExit("Collection came back empty - refusing to overwrite existing data.")

    details = fetch_things(sorted(merged.keys(), key=int))

    want_covers = "--no-covers" not in sys.argv
    out = []
    for oid, game in merged.items():
        game.update(details.get(oid, {}))
        if want_covers:
            info = cover_for(game)
            if info:
                game.update(info)
        game["url"] = "https://boardgamegeek.com/boardgame/{}".format(game["id"])
        game.pop("thumb", None)
        out.append(game)

    out.sort(key=lambda g: (g["name"] or "").lower())
    if want_covers:
        prune_covers({str(g["id"]) for g in out})

    payload = {
        "username": USERNAME,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {
            "games": sum(1 for g in out if not g["isExpansion"]),
            "expansions": sum(1 for g in out if g["isExpansion"]),
            "plays": sum(g.get("plays") or 0 for g in out),
        },
        "games": out,
    }
    target = DATA / "collection.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
    log("Wrote {} ({} games, {} expansions)".format(
        target, payload["counts"]["games"], payload["counts"]["expansions"]))


if __name__ == "__main__":
    main()
