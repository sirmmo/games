# games

My board game collection, synced daily from [BoardGameGeek](https://boardgamegeek.com/user/sirmmo)
and shelved on a Kallax.

**→ [ingmmo.com/games](https://ingmmo.com/games/)**

(`sirmmo.github.io/games` redirects there — the account-level custom domain wins.)

Every owned game gets a cubby, box art facing out. Search by title, designer or
mechanic; filter by player count, play time and weight; sort by rating, rank,
year or plays. Click a box for the details. The shelf comes in white, oak and
black-brown, because of course it does.

## How it works

A GitHub Action runs daily at 05:17 UTC. It picks a source, builds
`site/data/collection.json`, commits it, and publishes `site/` to Pages.

Box art is downloaded **once per game**, re-encoded to WebP at 600px and cached
in `site/covers/`. The dominant colour of each box is extracted at the same time,
desaturated and normalised to a constant luminance, and used to tint the cubby
behind it — which is why every recess reads as the same depth.

The site is plain HTML, CSS and JavaScript: no build step, no dependencies, and
no runtime calls to BGG. Covers are served from this repo, never hotlinked.

### Two sources

**`scripts/import_csv.py` — a CSV export, no credentials needed.** This is what
the shelf currently runs on. Export from
`boardgamegeek.com/collection/user/<you>` via *Download as CSV*, commit it as
`collection.csv`, and the importer reads ratings, plays, rank, weight, player
counts, play time and BGG's best/recommended player poll straight out of it.
Box art, designers, mechanics and blurbs come from `api.geekdo.com` — the JSON
API the BGG website itself uses, which needs no token. It also recovers which
edition of each game is on the shelf, and links owned expansions to the owned
game they expand.

The catch: a CSV is a snapshot. The shelf only changes when you commit a fresh
one, so the daily job is effectively a no-op until a token exists.

**`scripts/sync_bgg.py` — the XML API, needs a token.** This is the real daily
sync. BoardGameGeek closed the XML API to anonymous requests in October 2025, so
unauthenticated calls return `401 Unauthorized`.

1. Register at
   [boardgamegeek.com/using_the_xml_api](https://boardgamegeek.com/using_the_xml_api).
2. Add it under **Settings → Secrets and variables → Actions → New repository
   secret**, named `BGG_TOKEN`.
3. Run the **Sync BGG collection** workflow.

The workflow uses the API whenever `BGG_TOKEN` is set and falls back to the CSV
otherwise, so adding the secret is the only step needed to switch over. Both
importers are `continue-on-error`: if a refresh fails, the site still deploys
with the last known-good collection, so a bad token never empties the shelf.

To shelve someone else's collection, set a repository **variable** (not a
secret) called `BGG_USERNAME`.

## Running it locally

```sh
pip install -r scripts/requirements.txt

python scripts/import_csv.py collection.csv     # no token needed
BGG_TOKEN=... python scripts/sync_bgg.py        # once you have one

python -m http.server -d site 8000              # then open localhost:8000
```

Both take `--no-covers` to skip image work; `import_csv.py` also takes
`--limit N` for a quick trial run. Details already fetched are reused from
`site/data/collection.json`, so re-runs only hit the network for new games.

## Layout

```
collection.csv               BGG collection export (the current source)
scripts/bggshelf.py          shared: covers, tints, text cleanup, output
scripts/import_csv.py        CSV + api.geekdo.com  → JSON (no token)
scripts/sync_bgg.py          BGG XML API           → JSON (needs a token)
site/index.html              the shelf
site/assets/style.css        Kallax geometry: 33.5cm cubby in a 3.9cm frame
site/assets/app.js           filtering, sorting, detail view
site/data/collection.json    generated, committed
site/covers/<id>.webp        generated, committed
.github/workflows/sync.yml   daily refresh + Pages deploy
```

## Credits

Game data and box art come from BoardGameGeek via its
[XML API](https://boardgamegeek.com/using_the_xml_api) and remain © their
respective publishers. Kallax is IKEA's, and holds these games in real life too.
