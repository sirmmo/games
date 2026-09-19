/* sirmmo's shelf — renders data/collection.json into a Kallax. No build step. */
(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const shelf = $('#kallax');
  const dialog = $('#detail');

  const state = { games: [], view: [] };

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));

  const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);

  /* ----------------------------------------------------------------- finish */

  const FINISHES = ['white', 'oak', 'black'];

  function setFinish(name, persist) {
    if (!FINISHES.includes(name)) name = 'white';
    document.documentElement.dataset.finish = name;
    document.querySelectorAll('.finish__swatch').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.finish === name));
    });
    if (persist) { try { localStorage.setItem('shelf-finish', name); } catch (e) { /* private mode */ } }
  }

  document.querySelectorAll('.finish__swatch').forEach((b) => {
    b.addEventListener('click', () => setFinish(b.dataset.finish, true));
  });

  let saved = null;
  try { saved = localStorage.getItem('shelf-finish'); } catch (e) { /* ignore */ }
  setFinish(saved || 'white', false);

  /* ---------------------------------------------------------------- filtering */

  function playtimeOf(g) {
    return num(g.playingTime) || num(g.maxPlaytime) || num(g.minPlaytime) || null;
  }

  function matchesPlayers(g, n) {
    if (!n) return true;
    const lo = num(g.minPlayers), hi = num(g.maxPlayers);
    if (lo === null && hi === null) return false;
    if (n === 7) return (hi ?? lo) >= 7;
    return (lo ?? 1) <= n && n <= (hi ?? 99);
  }

  function inRange(value, spec) {
    if (!spec) return true;
    if (value === null) return false;
    const [lo, hi] = spec.split('-').map(Number);
    return value > lo && value <= hi;
  }

  function haystack(g) {
    if (!g._hay) {
      g._hay = [g.name, g.originalName, g.year, g.edition,
        ...(g.designers || []), ...(g.mechanics || []),
        ...(g.categories || []), ...(g.publishers || [])].join(' ').toLowerCase();
    }
    return g._hay;
  }

  const SORTS = {
    name: (a, b) => (a.name || '').localeCompare(b.name || '', undefined, { sensitivity: 'base' }),
    rating: (a, b) => (num(b.rating) ?? -1) - (num(a.rating) ?? -1),
    myrating: (a, b) => (num(b.myRating) ?? -1) - (num(a.myRating) ?? -1),
    rank: (a, b) => (num(a.rank) ?? 1e9) - (num(b.rank) ?? 1e9),
    year: (a, b) => (num(b.year) ?? -9999) - (num(a.year) ?? -9999),
    plays: (a, b) => (num(b.plays) ?? 0) - (num(a.plays) ?? 0),
    weight: (a, b) => (num(b.weight) ?? -1) - (num(a.weight) ?? -1),
  };

  function apply() {
    const q = $('#q').value.trim().toLowerCase();
    const players = Number($('#players').value) || 0;
    const time = $('#time').value;
    const weight = $('#weight').value;
    const sortKey = $('#sort').value;
    const withExp = $('#show-exp').checked;

    const terms = q ? q.split(/\s+/) : [];

    state.view = state.games.filter((g) => {
      if (!withExp && g.isExpansion) return false;
      if (!matchesPlayers(g, players)) return false;
      if (!inRange(playtimeOf(g), time)) return false;
      if (!inRange(num(g.weight), weight)) return false;
      if (terms.length) {
        const hay = haystack(g);
        if (!terms.every((t) => hay.includes(t))) return false;
      }
      return true;
    });

    const cmp = SORTS[sortKey] || SORTS.name;
    state.view.sort((a, b) => cmp(a, b) || SORTS.name(a, b));

    render();
  }

  /* ---------------------------------------------------------------- rendering */

  function cubbyHTML(g) {
    const src = g.cover || g.image || '';
    const dims = g.w && g.h ? ` width="${g.w}" height="${g.h}"` : '';
    // Pre-blended by the sync script, so no color-mix() dependency here.
    const tint = g.tint ? ` style="--cubby-tint: ${esc(g.tint)}"` : '';
    const mine = num(g.myRating);
    const chip = mine
      ? `<span class="cubby__chip cubby__chip--rated">${mine.toFixed(mine % 1 ? 1 : 0)}</span>`
      : '';
    const owned = g.ownedExpansions || 0;
    const exp = g.isExpansion
      ? '<span class="cubby__exp" title="Expansion">+</span>'
      : (owned ? `<span class="cubby__exp cubby__exp--owned" title="${owned} expansion${owned > 1 ? 's' : ''} on the shelf">+${owned}</span>` : '');
    const year = g.year ? ` <span class="cubby__year">${esc(g.year)}</span>` : '';

    const art = src
      ? `<img class="cubby__box" src="${esc(src)}" alt=""${dims} loading="lazy" decoding="async">`
      : '<span class="cubby__box" aria-hidden="true"></span>';

    return `<button type="button" class="cubby" data-id="${esc(g.id)}"${tint}>
      ${art}${chip}${exp}
      <span class="cubby__plate"><span>${esc(g.name)}${year}</span></span>
    </button>`;
  }

  function columnCount() {
    const w = window.innerWidth;
    if (w >= 1140) return 5;
    if (w >= 860) return 4;
    if (w >= 560) return 3;
    return 2;
  }

  function render() {
    const list = state.view;
    $('#resultline').textContent = list.length === state.games.length
      ? `${list.length} on the shelf`
      : `${list.length} of ${state.games.length} on the shelf`;

    if (!state.games.length) {
      shelf.innerHTML = '';
      shelf.setAttribute('aria-busy', 'false');
      $('#empty').hidden = false;
      $('#empty').innerHTML = 'Nothing on the shelf yet — the first sync has not run. '
        + 'Add a <code>BGG_TOKEN</code> secret to the repository, then run the '
        + '<b>Sync BGG collection</b> workflow.';
      return;
    }

    $('#empty').hidden = list.length > 0;
    if (!list.length) {
      $('#empty').textContent = 'No games match those filters.';
    }

    // Pad the final row so the unit always reads as a complete Kallax.
    const cols = columnCount();
    const pad = list.length % cols ? cols - (list.length % cols) : 0;

    shelf.innerHTML = list.map(cubbyHTML).join('')
      + '<div class="cubby cubby--empty" aria-hidden="true"></div>'.repeat(pad);
    shelf.setAttribute('aria-busy', 'false');
  }

  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.view.length) render(); }, 150);
  });

  /* ------------------------------------------------------------------- modal */

  function fact(label, value) {
    return value ? `<li>${esc(label)} <b>${esc(value)}</b></li>` : '';
  }

  function players(g) {
    const lo = num(g.minPlayers), hi = num(g.maxPlayers);
    if (!lo && !hi) return '';
    return lo === hi ? `${lo}` : `${lo ?? '?'}–${hi ?? '?'}`;
  }

  function time(g) {
    const lo = num(g.minPlaytime), hi = num(g.maxPlaytime), one = num(g.playingTime);
    if (lo && hi && lo !== hi) return `${lo}–${hi} min`;
    return one || hi || lo ? `${one || hi || lo} min` : '';
  }

  function openDetail(id) {
    const g = state.games.find((x) => String(x.id) === String(id));
    if (!g) return;

    const tint = g.tint ? ` style="--detail-tint: ${esc(g.tint)}"` : '';
    const src = g.cover || g.image || '';
    const best = (g.bestWith || []).length ? `best with ${g.bestWith.join(', ')}` : '';

    const meta = [
      g.isExpansion ? 'Expansion' : null,
      g.year || null,
      (g.designers || []).slice(0, 3).join(', ') || null,
    ].filter(Boolean).join(' · ');

    const alt = g.originalName && g.originalName !== g.name ? g.originalName : '';

    dialog.querySelector('.detail__body').innerHTML = `
      <div class="detail__art"${tint}>
        ${src ? `<img src="${esc(src)}" alt="Box art for ${esc(g.name)}">` : ''}
      </div>
      <div class="detail__text">
        <h2 id="detail-title">${esc(g.name)}</h2>
        ${alt ? `<p class="detail__alt">${esc(alt)}</p>` : ''}
        <p class="detail__meta">${esc(meta)}</p>
        <ul class="facts">
          ${fact('Players', players(g))}
          ${best ? `<li>${esc(best)}</li>` : ''}
          ${fact('Time', time(g))}
          ${fact('Weight', num(g.weight) ? `${g.weight.toFixed(2)}/5` : '')}
          ${fact('BGG', num(g.rating) ? g.rating.toFixed(1) : '')}
          ${fact('My rating', num(g.myRating) ? String(g.myRating) : '')}
          ${fact('Rank', num(g.rank) ? `#${g.rank}` : '')}
          ${fact('Plays', g.plays ? String(g.plays) : '')}
          ${fact('Expansions', g.ownedExpansions ? String(g.ownedExpansions) : '')}
          ${fact('Copies', g.copies > 1 ? String(g.copies) : '')}
        </ul>
        ${g.description ? `<p class="detail__desc">${esc(g.description)}</p>` : ''}
        ${(g.mechanics || []).length ? `<p class="tags"><b>Mechanics</b> ${esc(g.mechanics.slice(0, 8).join(' · '))}</p>` : ''}
        ${(g.expands || []).length ? `<p class="tags"><b>Expands</b> ${esc(g.expands.map((e) => e.name).join(' · '))}</p>` : ''}
        ${g.edition ? `<p class="tags"><b>My copy</b> ${esc(g.edition)}</p>` : ''}
        ${g.comment ? `<p class="tags"><b>Note</b> ${esc(g.comment)}</p>` : ''}
        <a class="detail__link" href="${esc(g.url)}" target="_blank" rel="noopener">View on BoardGameGeek →</a>
      </div>`;

    if (!dialog.open) dialog.showModal();
    history.replaceState(null, '', `#g${g.id}`);
  }

  shelf.addEventListener('click', (e) => {
    const btn = e.target.closest('.cubby');
    if (btn && btn.dataset.id) openDetail(btn.dataset.id);
  });

  $('.detail__close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => {
    if (location.hash.startsWith('#g')) history.replaceState(null, '', location.pathname + location.search);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === '/' && document.activeElement !== $('#q')) {
      e.preventDefault();
      $('#q').focus();
      $('#q').select();
    }
  });

  /* -------------------------------------------------------------------- boot */

  ['#q', '#players', '#time', '#weight', '#sort', '#show-exp'].forEach((sel) => {
    const el = $(sel);
    el.addEventListener(el.tagName === 'INPUT' && el.type === 'search' ? 'input' : 'change', apply);
  });

  fetch('data/collection.json', { cache: 'no-cache' })
    .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
    .then((data) => {
      state.games = Array.isArray(data.games) ? data.games : [];

      const c = data.counts || {};
      $('#stat-games').textContent = c.games ?? state.games.length;
      $('#stat-exp').textContent = c.expansions ?? 0;
      $('#stat-plays').textContent = c.plays ?? 0;

      if (data.generatedAt) {
        const d = new Date(data.generatedAt);
        const el = $('#synced');
        el.dateTime = data.generatedAt;
        el.textContent = d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
      }

      apply();

      const m = location.hash.match(/^#g(\d+)$/);
      if (m) openDetail(m[1]);
    })
    .catch((err) => {
      shelf.setAttribute('aria-busy', 'false');
      $('#empty').hidden = false;
      $('#empty').textContent = `Could not load the collection (${err.message}).`;
    });
})();
