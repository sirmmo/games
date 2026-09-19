"""Shared pieces for building the shelf, whatever the collection came from.

Two importers feed the same `site/data/collection.json`:
  sync_bgg.py    the XML API, which needs a registered bearer token
  import_csv.py  a collection CSV exported from the website, which needs nothing
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

USER_AGENT = "sirmmo-games-kallax/1.0 (+https://github.com/sirmmo/games)"

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
COVERS = SITE / "covers"
DATA = SITE / "data"
COLLECTION = DATA / "collection.json"

COVER_HEIGHT = 600
COVER_QUALITY = 82


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------ text ----

def clean(text):
    """Flatten a BGG description to plain text.

    BGG double-encodes these, so a parser that decodes one layer still leaves
    literal entities like `&#10;` behind; unescape again before stripping tags.
    """
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"


def as_int(value, default=None, zero_is_none=True):
    """BGG's CSV writes an absent number as 0 rather than leaving it blank."""
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    if zero_is_none and n == 0:
        return default
    return n


def as_float(value, default=None, zero_is_none=True):
    try:
        n = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if zero_is_none and n == 0:
        return default
    return n


# ----------------------------------------------------------------- http ----

def http_bytes(url, timeout=90, headers=None, attempts=4):
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    delay = 2.0
    last = None
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise last


# ---------------------------------------------------------------- covers ----

def shelf_tint(rgb):
    """Turn a box's dominant colour into a shadowed cubby interior.

    Desaturated and normalised to a constant luminance so every recess reads as
    the same depth however bright the box is, and so the result is a plain hex
    colour rather than a color-mix() the browser may not support.
    """
    r, g, b = [c / 255.0 for c in rgb]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    r, g, b = [luma + (c - luma) * 0.55 for c in (r, g, b)]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    scale = min(0.20 / max(luma, 0.02), 6.0)
    out = [min(255, max(0, int(round(c * scale * 255)))) for c in (r, g, b)]
    return "#{:02x}{:02x}{:02x}".format(*out)


def cover_for(game_id, image_url, label=""):
    """Download box art once, store it as WebP, and note its dominant colour.

    The CDN signs the resize path of its URLs, so a correctly sized variant
    cannot be synthesised — whatever we are given is fetched and re-encoded.
    """
    from PIL import Image

    COVERS.mkdir(parents=True, exist_ok=True)
    dest = COVERS / "{}.webp".format(game_id)
    meta = COVERS / "{}.json".format(game_id)
    if dest.exists() and meta.exists():
        try:
            return json.loads(meta.read_text())
        except ValueError:
            pass
    if not image_url:
        return None

    # pid-scoped so two importers running at once cannot clobber each other
    tmp = COVERS / "{}.{}.tmp".format(game_id, os.getpid())
    try:
        tmp.write_bytes(http_bytes(image_url))
    except Exception as e:
        log("  ! cover download failed for {} ({}): {}".format(label, game_id, e))
        return None

    try:
        with Image.open(tmp) as im:
            im = im.convert("RGBA")
            flat = Image.new("RGBA", im.size, (255, 255, 255, 255))
            flat.alpha_composite(im)
            rgb = flat.convert("RGB")

            w, h = rgb.size
            if h > COVER_HEIGHT:
                rgb = rgb.resize((max(1, int(round(w * COVER_HEIGHT / h))), COVER_HEIGHT), Image.LANCZOS)

            tint = rgb.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
            rgb.save(dest, "WEBP", quality=COVER_QUALITY, method=6)
            info = {
                "cover": "covers/{}.webp".format(game_id),
                "w": rgb.size[0],
                "h": rgb.size[1],
                "tint": shelf_tint(tint),
            }
            meta.write_text(json.dumps(info))
            return info
    except Exception as e:
        log("  ! cover processing failed for {} ({}): {}".format(label, game_id, e))
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def prune_covers(keep_ids):
    removed = 0
    for f in COVERS.glob("*"):
        if f.suffix in (".webp", ".json") and f.stem not in keep_ids:
            f.unlink()
            removed += 1
    if removed:
        log("Pruned {} orphaned cover files".format(removed))


# ------------------------------------------------------------------ data ----

def load_existing():
    """Previous output doubles as the enrichment cache: static per-game facts
    (art, credits, description) are fetched once and carried forward."""
    try:
        data = json.loads(COLLECTION.read_text())
    except (IOError, OSError, ValueError):
        return {}
    return {str(g["id"]): g for g in data.get("games", []) if g.get("id") is not None}


def write_collection(games, username, source):
    DATA.mkdir(parents=True, exist_ok=True)
    games.sort(key=lambda g: (g.get("name") or "").lower())
    payload = {
        "username": username,
        "source": source,
        "generatedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "counts": {
            "games": sum(1 for g in games if not g.get("isExpansion")),
            "expansions": sum(1 for g in games if g.get("isExpansion")),
            "plays": sum(g.get("plays") or 0 for g in games),
        },
        "games": games,
    }
    COLLECTION.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
    log("Wrote {} — {} games, {} expansions, {} plays (via {})".format(
        COLLECTION, payload["counts"]["games"], payload["counts"]["expansions"],
        payload["counts"]["plays"], source))
    return payload
