/* Bulana Property Finder - loads data/listings.json and does all searching,
   filtering, sorting and exporting in the browser. */
(() => {
  'use strict';

  const PAGE = 48;
  const SRC_NAME = { ikman: 'ikman.lk', lpw: 'LankaPropertyWeb' };
  const TYPE_NAME = { house: 'House', land: 'Land', apartment: 'Apartment', room: 'Room / annex' };
  const $ = (id) => document.getElementById(id);

  let all = [];          // every listing
  let meta = {};
  let shown = [];        // current result groups: [{ item, others: [] }]
  let flat = [];         // current results, ungrouped (for export)
  let rendered = 0;

  /* ---------- helpers ---------- */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => Number(n).toLocaleString('en-US');

  function money(n) {
    if (n == null) return '';
    if (n >= 1e9) return 'Rs ' + trim(n / 1e9) + ' Bn';
    if (n >= 1e6) return 'Rs ' + trim(n / 1e6) + ' Mn';
    return 'Rs ' + num(n);
  }
  const trim = (x) => (x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(2)).replace(/\.?0+$/, '');

  const UNIT = { total: '', perch: 'per perch', acre: 'per acre', sqft: 'per sq ft', month: 'per month', day: 'per day', year: 'per year' };

  /** "45m", "4.5 mn", "50 lakh", "150k", "25,000,000" -> number (or null). */
  function parseAmount(text) {
    const t = String(text || '').toLowerCase().replace(/,/g, '').replace(/rs\.?/g, '').trim();
    const m = t.match(/^(\d+(?:\.\d+)?)\s*(k|m|mn|million|b|bn|billion|l|lakh|lakhs|lac)?$/);
    if (!m) return null;
    const mult = { k: 1e3, m: 1e6, mn: 1e6, million: 1e6, b: 1e9, bn: 1e9, billion: 1e9, l: 1e5, lakh: 1e5, lakhs: 1e5, lac: 1e5 }[m[2]] || 1;
    return parseFloat(m[1]) * mult;
  }

  function dayDiff(a, b) { return Math.round((Date.parse(a) - Date.parse(b)) / 864e5); }
  function niceDate(iso) {
    return new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
  }

  /* ---------- load ---------- */
  function expand(o) {
    const url = o.url || '', img = o.img || '';
    if (url[0] === '~') {
      const slug = url.slice(1);
      o.url = 'https://ikman.lk/en/ad/' + slug;
      o.img = img[0] === '~' ? `https://i.ikman-st.com/${slug}/${img.slice(1)}/300/225/cropped.jpg` : img.replace('/142/107/', '/300/225/');
    } else if (url[0] === '^') {
      o.url = 'https://www.lankapropertyweb.com' + url.slice(1);
      o.img = img[0] === '^' ? 'https://www.lankapropertyweb.com' + img.slice(1) : img;
    }
    // Price used for filtering and sorting: whole price for sales, the rent itself for rentals.
    o.cmp = o.deal === 'sale' ? (o.total ?? (o.unit === 'total' ? o.price : null)) : o.price;
    o.isNew = o.first > meta.baseline;
    o.age = dayDiff(meta.today, o.first);
    o.pct = o.prev && o.price ? (o.price - o.prev) / o.prev * 100 : 0;
    o.recentChange = !!(o.changed && o.pct && dayDiff(meta.today, o.changed) <= 14);
    o.hay = [o.title, o.city, o.district, o.addr, o.seller, TYPE_NAME[o.type], o.deal === 'sale' ? 'sale buy' : 'rent rental lease',
      o.beds ? `${o.beds} bed ${o.beds} bedroom ${o.beds}br` : '', o.perches ? `${o.perches} perch` : ''].join(' ').toLowerCase();
    return o;
  }

  async function load() {
    try {
      const res = await fetch('data/listings.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(res.status);
      const raw = await res.json();
      meta = raw.meta;
      const cols = raw.columns;
      all = raw.rows.map((r) => {
        const o = {};
        for (let i = 0; i < cols.length; i++) o[cols[i]] = r[i];
        return expand(o);
      });
    } catch (e) {
      $('status').innerHTML = '<strong>No listings yet.</strong><br>The daily update has not produced a data file. Run the scraper once, then reload this page.';
      return;
    }
    fillDistricts();
    readUrl();
    fillCities();
    $('status').hidden = true;
    $('toolbar').hidden = false;
    const when = new Date(meta.updated);
    $('updated').textContent = `Last updated ${when.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}` +
      ` · ${num(meta.total)} listings · ${num(meta.new_today)} new today · ${num(meta.price_changes_today)} price changes today`;
    apply();
  }

  function fillDistricts() {
    const sel = $('district');
    for (const d of meta.districts || []) sel.add(new Option(d, d));
  }

  function fillCities() {
    const district = $('district').value;
    const keep = $('city').value;
    const counts = new Map();
    for (const o of all) {
      if (district && o.district !== district) continue;
      if (o.city) counts.set(o.city, (counts.get(o.city) || 0) + 1);
    }
    const sel = $('city');
    sel.length = 1;
    [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0], 'en', { numeric: true }))
      .forEach(([c, n]) => sel.add(new Option(`${c} (${num(n)})`, c)));
    sel.value = counts.has(keep) ? keep : '';
  }

  /* ---------- filter state ---------- */
  function state() {
    return {
      q: $('q').value.trim(),
      deal: document.querySelector('.seg .on')?.dataset.deal || '',
      types: [...document.querySelectorAll('[data-type]:checked')].map((c) => c.dataset.type),
      district: $('district').value,
      city: $('city').value,
      pmin: parseAmount($('price-min').value),
      pmax: parseAmount($('price-max').value),
      beds: +$('beds').value || 0,
      smin: parseFloat($('perch-min').value) || null,
      smax: parseFloat($('perch-max').value) || null,
      fresh: $('fresh').value,
      source: $('source').value,
      changed: $('changed').checked,
      group: $('group').checked,
      sort: $('sort').value,
    };
  }

  const SORTS = {
    'new': (a, b) => (a.first < b.first ? 1 : a.first > b.first ? -1 : 0),
    'price-asc': (a, b) => (a.cmp ?? Infinity) - (b.cmp ?? Infinity),
    'price-desc': (a, b) => (b.cmp ?? -1) - (a.cmp ?? -1),
    'size-desc': (a, b) => (b.perches ?? -1) - (a.perches ?? -1),
    'drop': (a, b) => (a.recentChange ? a.pct : 0) - (b.recentChange ? b.pct : 0),
  };

  function apply() {
    const s = state();
    const words = s.q.toLowerCase().split(/\s+/).filter(Boolean);
    const types = new Set(s.types);

    flat = all.filter((o) => {
      if (s.deal && o.deal !== s.deal) return false;
      if (types.size && !types.has(o.type)) return false;
      if (s.district && o.district !== s.district) return false;
      if (s.city && o.city !== s.city) return false;
      if (s.source && o.src !== s.source) return false;
      if (s.beds && !(o.beds >= s.beds)) return false;
      if (s.pmin != null && !(o.cmp >= s.pmin)) return false;
      if (s.pmax != null && !(o.cmp <= s.pmax)) return false;
      if (s.smin != null && !(o.perches >= s.smin)) return false;
      if (s.smax != null && !(o.perches <= s.smax)) return false;
      if (s.fresh !== '' && !(o.isNew && o.age <= +s.fresh)) return false;
      if (s.changed && !o.recentChange) return false;
      for (const w of words) if (!o.hay.includes(w)) return false;
      return true;
    });
    flat.sort(SORTS[s.sort] || SORTS.new);

    if (s.group) {
      const seen = new Map();
      shown = [];
      for (const o of flat) {
        if (o.dup && seen.has(o.dup)) { seen.get(o.dup).others.push(o); continue; }
        const g = { item: o, others: [] };
        if (o.dup) seen.set(o.dup, g);
        shown.push(g);
      }
    } else {
      shown = flat.map((o) => ({ item: o, others: [] }));
    }

    const hiddenDups = flat.length - shown.length;
    $('count').innerHTML = `${num(shown.length)} ${shown.length === 1 ? 'property' : 'properties'}` +
      (hiddenDups ? `<small>${num(hiddenDups)} duplicate ads grouped</small>` : '');
    $('cards').innerHTML = shown.length ? '' :
      '<div class="empty"><strong>No properties match.</strong>Try fewer words, a wider price range, or clear the filters.</div>';
    rendered = 0;
    renderMore();
    writeUrl(s);
  }

  /* ---------- cards ---------- */
  function priceLine(o) {
    if (!o.price) return '<span class="unit">Price on request</span>';
    const unit = UNIT[o.unit] ? ` <span class="unit">${UNIT[o.unit]}</span>` : '';
    return money(o.price) + unit;
  }

  function card({ item: o, others }) {
    const badges = [];
    if (o.isNew && o.age <= 6) badges.push(`<span class="badge new">${o.age === 0 ? 'New today' : 'New · ' + niceDate(o.first)}</span>`);
    if (o.recentChange) {
      const down = o.pct < 0;
      badges.push(`<span class="badge ${down ? 'down' : 'up'}">${down ? '↓' : '↑'} ${Math.abs(o.pct).toFixed(Math.abs(o.pct) < 10 ? 1 : 0)}%</span>`);
    }
    const notes = [];
    if (o.recentChange) notes.push(`was <s>${money(o.prev)}</s> until ${niceDate(o.changed)}`);
    if (o.total && o.unit !== 'total') notes.push(`about ${money(o.total)} in total`);

    const facts = [];
    facts.push(TYPE_NAME[o.type] + (o.deal === 'rent' ? ' for rent' : ' for sale'));
    if (o.beds) facts.push(`${o.beds} bed`);
    if (o.baths) facts.push(`${o.baths} bath`);
    if (o.perches) facts.push(`${num(o.perches)} perches`);
    if (o.sqft) facts.push(`${num(o.sqft)} sq ft`);

    const where = [o.addr && !o.addr.endsWith('..') ? o.addr : '', o.city, o.district !== o.city ? o.district : '']
      .filter((x, i, a) => x && a.indexOf(x) === i && !(i > 0 && a[0] && a[0].includes(x))).join(', ');

    const dups = others.length ? `<details class="dups"><summary>${others.length} similar ${others.length === 1 ? 'ad' : 'ads'}</summary><ul>` +
      others.slice(0, 12).map((d) => `<li><a href="${esc(d.url)}" target="_blank" rel="noopener">${SRC_NAME[d.src]}: ${esc(d.title)}</a></li>`).join('') +
      '</ul></details>' : '';

    return `<article class="card">
      <a class="thumb" href="${esc(o.url)}" target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">
        ${o.img ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(o.img)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'noimg',textContent:'No photo'}))">` : '<span class="noimg">No photo</span>'}
        <span class="badges">${badges.join('')}</span>
      </a>
      <div class="body">
        <div class="price">${priceLine(o)}</div>
        ${notes.length ? `<div class="price-note">${notes.join(' · ')}</div>` : ''}
        <a class="title" href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a>
        <div class="where">${esc(where)}</div>
        <div class="facts">${facts.map((f) => `<span class="fact">${esc(f)}</span>`).join('')}</div>
        ${dups}
        <div class="meta">
          <a class="src" href="${esc(o.url)}" target="_blank" rel="noopener">View on ${SRC_NAME[o.src]} ↗</a>
          <span>${o.isNew ? 'First seen ' + niceDate(o.first) : ''}</span>
        </div>
      </div>
    </article>`;
  }

  function renderMore() {
    const next = shown.slice(rendered, rendered + PAGE);
    $('cards').insertAdjacentHTML('beforeend', next.map(card).join(''));
    rendered += next.length;
    const left = shown.length - rendered;
    $('more').hidden = left <= 0;
    $('more').textContent = `Show more (${num(left)} left)`;
  }

  /* ---------- export ---------- */
  function exportRows() {
    return flat.map((o) => ({
      'Title': o.title,
      'For': o.deal === 'sale' ? 'Sale' : 'Rent',
      'Type': TYPE_NAME[o.type],
      'Town': o.city,
      'District': o.district,
      'Address': o.addr,
      'Price (Rs)': o.price ?? '',
      'Price is': UNIT[o.unit] || 'total',
      'Total price (Rs)': o.total ?? '',
      'Bedrooms': o.beds ?? '',
      'Bathrooms': o.baths ?? '',
      'Land (perches)': o.perches ?? '',
      'Floor area (sq ft)': o.sqft ?? '',
      'Previous price (Rs)': o.prev ?? '',
      'Price changed on': o.changed ?? '',
      'First seen': o.first,
      'Last seen': o.last,
      'Website': SRC_NAME[o.src],
      'Advertiser': o.seller,
      'Link': o.url,
    }));
  }

  function download(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function doExport() {
    const rows = exportRows();
    if (!rows.length) return;
    const name = `bulana-properties-${meta.today}`;
    if (window.XLSX) {
      const ws = XLSX.utils.json_to_sheet(rows);
      ws['!cols'] = Object.keys(rows[0]).map((k) => ({ wch: k === 'Title' || k === 'Link' ? 50 : Math.max(12, k.length + 2) }));
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, 'Properties');
      XLSX.writeFile(wb, name + '.xlsx');
    } else {
      // Spreadsheet library unavailable (offline): fall back to CSV, which Excel also opens.
      const keys = Object.keys(rows[0]);
      const cell = (v) => `"${String(v).replace(/"/g, '""')}"`;
      const csv = [keys.map(cell).join(','), ...rows.map((r) => keys.map((k) => cell(r[k])).join(','))].join('\r\n');
      download(new Blob(['﻿' + csv], { type: 'text/csv' }), name + '.csv');
    }
  }

  /* ---------- shareable URL ---------- */
  const URL_KEYS = { q: 'q', district: 'district', city: 'city', 'price-min': 'min', 'price-max': 'max', beds: 'beds', 'perch-min': 'smin', 'perch-max': 'smax', fresh: 'new', source: 'site', sort: 'sort' };

  function writeUrl(s) {
    const p = new URLSearchParams();
    for (const [id, key] of Object.entries(URL_KEYS)) {
      const v = $(id).value.trim();
      if (v && !(id === 'sort' && v === 'new')) p.set(key, v);
    }
    if (s.deal) p.set('deal', s.deal);
    if (s.types.length) p.set('type', s.types.join(','));
    if (s.changed) p.set('changed', '1');
    if (!s.group) p.set('group', '0');
    const qs = p.toString();
    history.replaceState(null, '', qs ? '?' + qs : location.pathname);
  }

  function readUrl() {
    const p = new URLSearchParams(location.search);
    for (const [id, key] of Object.entries(URL_KEYS)) if (p.has(key) && id !== 'city') $(id).value = p.get(key);
    fillCities();
    if (p.has('city')) $('city').value = p.get('city');
    setDeal(p.get('deal') || '');
    const types = (p.get('type') || '').split(',');
    document.querySelectorAll('[data-type]').forEach((c) => { c.checked = types.includes(c.dataset.type); });
    $('changed').checked = p.get('changed') === '1';
    $('group').checked = p.get('group') !== '0';
  }

  function setDeal(deal) {
    document.querySelectorAll('.seg button').forEach((b) => b.classList.toggle('on', b.dataset.deal === deal));
  }

  /* ---------- events ---------- */
  let timer;
  const soon = () => { clearTimeout(timer); timer = setTimeout(apply, 180); };

  $('search-form').addEventListener('submit', (e) => { e.preventDefault(); apply(); });
  ['q', 'price-min', 'price-max', 'perch-min', 'perch-max'].forEach((id) => $(id).addEventListener('input', soon));
  ['city', 'beds', 'fresh', 'source', 'changed', 'group', 'sort'].forEach((id) => $(id).addEventListener('change', apply));
  $('district').addEventListener('change', () => { fillCities(); apply(); });
  document.querySelectorAll('[data-type]').forEach((c) => c.addEventListener('change', apply));
  document.querySelectorAll('.seg button').forEach((b) => b.addEventListener('click', () => { setDeal(b.dataset.deal); apply(); }));
  $('more').addEventListener('click', renderMore);
  $('export').addEventListener('click', doExport);
  $('filters-toggle').addEventListener('click', () => {
    const open = $('filters').classList.toggle('open');
    $('filters-toggle').setAttribute('aria-expanded', open);
  });
  $('reset').addEventListener('click', () => {
    ['q', 'district', 'price-min', 'price-max', 'beds', 'perch-min', 'perch-max', 'fresh', 'source'].forEach((id) => { $(id).value = ''; });
    $('sort').value = 'new';
    document.querySelectorAll('[data-type]').forEach((c) => { c.checked = false; });
    $('changed').checked = false;
    $('group').checked = true;
    setDeal('');
    fillCities();
    apply();
  });

  load();
})();
