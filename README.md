# games

My board game collection, synced daily from [BoardGameGeek](https://boardgamegeek.com/user/sirmmo)
and shelved on a Kallax.

**→ [sirmmo.github.io/games](https://sirmmo.github.io/games/)**

Every owned game gets a cubby, box art facing out. Search by title, designer or
mechanic; filter by player count, play time and weight; sort by rating, rank,
year or plays. Click a box for the details. The shelf comes in white, oak and
black-brown, because of course it does.

## How it works

A GitHub Action runs daily at 05:17 UTC:

1. `scripts/sync_bgg.py` pulls the owned collection from the BGG XML API
   (base games and expansions separately), then batches `/thing` lookups for
   designers, weight, rank, mechanics and the suggested-player-count poll.
2. Box art is downloaded **once per game**, downscaled to 600px and cached in
   `site/covers/` as WebP. The dominant colour of each box is extracted at the
   same time and used to tint the cubby behind it.
3. The result is written to `site/data/collection.json` and committed.
4. `site/` is published to GitHub Pages.

The site is plain HTML, CSS and JavaScript — no build step, no dependencies,
no runtime calls to BGG. Covers are served from this repo rather than hotlinked.

## Setup: the BGG API token

**This is required.** BoardGameGeek closed the XML API to anonymous requests in
October 2025 — unauthenticated calls now return `401 Unauthorized`.

1. Register for a token at
   [boardgamegeek.com/using_the_xml_api](https://boardgamegeek.com/using_the_xml_api).
2. Add it to this repo under **Settings → Secrets and variables → Actions →
   New repository secret**, named `BGG_TOKEN`.
3. Run the **Sync BGG collection** workflow manually to populate the shelf.

Every request sends `Authorization: Bearer $BGG_TOKEN`. If the secret is missing
or rejected, the sync step fails loudly but the site still deploys with the last
known-good collection, so a bad token never empties the shelf.

To shelve someone else's collection, set a repository **variable** (not secret)
called `BGG_USERNAME`.

## Running it locally

```sh
pip install -r scripts/requirements.txt
BGG_TOKEN=... python scripts/sync_bgg.py       # add --no-covers to skip image work
python -m http.server -d site 8000             # then open localhost:8000
```

## Layout

```
scripts/sync_bgg.py          BGG → JSON + cached covers
site/index.html              the shelf
site/assets/style.css        Kallax geometry: 33.5cm cubby in a 3.9cm frame
site/assets/app.js           filtering, sorting, detail view
site/data/collection.json    generated, committed
site/covers/<id>.webp        generated, committed
.github/workflows/sync.yml   daily sync + Pages deploy
```

## Credits

Game data and box art come from BoardGameGeek via its
[XML API](https://boardgamegeek.com/using_the_xml_api) and remain © their
respective publishers. Kallax is IKEA's, and holds these games in real life too.
