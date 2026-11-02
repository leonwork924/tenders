// Page « Mouvements corporates » : charge corporate_moves.json, calcule le score
// de chaque mouvement à partir de la grille du fichier (modifiable sans toucher
// au code) et affiche une liste filtrable. Pas de build, pas de framework.

const TIERS = ['HOT', 'WARM', 'WATCH', 'FAIBLE', 'ARCHIVE', 'ÉCARTÉ'];
const TIER_HELP = {
  HOT: 'Score élevé et mouvement récent ou à venir : à traiter en priorité',
  WARM: 'Probable mais incomplet (pays ou lieu à confirmer)',
  WATCH: 'Origine inconnue : à enrichir avant décision',
  FAIBLE: "Peu d'éléments : à surveiller seulement",
  ARCHIVE: 'Mouvement déjà effectif ou sans date : référence / calibrage',
  'ÉCARTÉ': 'Même pays avant et après : pas de lead',
};
const DEFAULT_TIERS = new Set(['HOT', 'WARM', 'WATCH', 'FAIBLE']);

const state = {
  q: '', upcomingOnly: false, sortKey: 'score', sortAsc: false,
  tiers: new Set(DEFAULT_TIERS), regions: new Set(), types: new Set(), open: new Set(),
};
let GRID = null;
let MOVES = [];

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function safeUrl(u) { return /^https?:\/\//i.test(u || '') ? u : ''; }
function known(v) { return v && v !== 'n/d'; }

const MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];
function fmtDate(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '');
  if (!m) return known(v) ? v : '';
  const d = parseInt(m[3], 10);
  return `${d === 1 ? '1er' : d} ${MONTHS[parseInt(m[2], 10) - 1]} ${m[1]}`;
}

// Zones détaillées -> grandes régions (filtre)
function macroRegion(zone) {
  const z = (zone || '').toLowerCase();
  if (z.startsWith('afrique')) return 'Afrique';
  if (z.includes('moyen-orient')) return 'Moyen-Orient';
  if (z.startsWith('asie') || z.includes('océanie') || z.includes('caucase')) return 'Asie-Pacifique & Caucase';
  if (z.startsWith('amérique')) return 'Amériques';
  if (z.startsWith('europe')) return 'Europe';
  return 'Autre';
}
function moveType(m) {
  if (m.sector === 'Diplomatie') return 'Diplomatie';
  if (m.sector === 'Organisation internationale') return 'Organisations internationales';
  return 'Entreprises';
}

function computePoints(m) {
  const P = GRID.points;
  return {
    pays: P.pays[m.pays] ?? 0,
    reloc: P.reloc[m.reloc] ?? 0,
    sen: P.sen[m.sen] ?? 0,
    sec: P.sec[m.sec] ?? 0,
    fresh: P.fresh[m.fresh] ?? 0,
  };
}
function computeTier(p, score) {
  if (p.pays < 0) return 'ÉCARTÉ';
  if (p.fresh === 0) return 'ARCHIVE';
  const T = GRID.tiers;
  if (score >= T.HOT) return 'HOT';
  if (score >= T.WARM) return 'WARM';
  if (score >= T.WATCH) return 'WATCH';
  return 'FAIBLE';
}
function sortDate(m) {
  return /^\d{4}-\d{2}-\d{2}$/.test(m.effective) ? m.effective : (/^\d{4}-\d{2}-\d{2}$/.test(m.announced) ? m.announced : '');
}

function prepare(raw) {
  return raw.map(m => {
    const pts = computePoints(m);
    const score = pts.pays + pts.reloc + pts.sen + pts.sec + pts.fresh;
    return {
      ...m, pts, score, tier: computeTier(pts, score),
      region: macroRegion(m.zone), type: moveType(m), date: sortDate(m),
      from: m.old_loc, to: m.new_loc,
      upcoming: /^2027/.test(m.effective || ''),
    };
  });
}

function matches(m) {
  if (!state.tiers.has(m.tier)) return false;
  if (state.regions.size && !state.regions.has(m.region)) return false;
  if (state.types.size && !state.types.has(m.type)) return false;
  if (state.upcomingOnly && !m.upcoming) return false;
  if (state.q) {
    const hay = [m.person, m.org, m.sector, m.old_role, m.old_loc, m.new_role, m.new_loc, m.zone, m.notes].join(' ').toLowerCase();
    if (!state.q.toLowerCase().split(/\s+/).every(t => hay.includes(t))) return false;
  }
  return true;
}

function toggleBtn(container, label, set, key, onChange, title) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'filter-btn' + (set.has(key) ? ' on' : '');
  b.textContent = label;
  if (title) b.title = title;
  b.onclick = () => { set.has(key) ? set.delete(key) : set.add(key); onChange(); };
  container.appendChild(b);
}

function renderFilters() {
  const counts = t => MOVES.filter(m => m.tier === t).length;
  const tf = document.getElementById('tier-filter');
  tf.innerHTML = '<span class="lbl">Niveau</span>';
  TIERS.forEach(t => toggleBtn(tf, `${t} (${counts(t)})`, state.tiers, t, refresh, TIER_HELP[t]));

  const rf = document.getElementById('region-filter');
  rf.innerHTML = '<span class="lbl">Destination</span>';
  [...new Set(MOVES.map(m => m.region))].sort().forEach(r => {
    const n = MOVES.filter(m => m.region === r && state.tiers.has(m.tier)).length;
    toggleBtn(rf, `${r} (${n})`, state.regions, r, refresh);
  });

  const yf = document.getElementById('type-filter');
  yf.innerHTML = '<span class="lbl">Type</span>';
  ['Organisations internationales', 'Entreprises'].forEach(t => toggleBtn(yf, t, state.types, t, refresh));
}

function dateCell(m) {
  const a = fmtDate(m.announced), e = fmtDate(m.effective);
  if (!a && !e) return '<span class="mv-sub">date n/d</span>';
  return `${a ? `Annoncé ${esc(a)}` : '<span class="mv-sub">annonce n/d</span>'}` +
         `${e ? `<span class="mv-sub">Prise de poste ${esc(e)}</span>` : ''}`;
}

function detailRow(m) {
  const p = m.pts;
  const f = n => `<span class="${n < 0 ? 'neg' : ''}">${n > 0 ? '+' : ''}${n}</span>`;
  const url = safeUrl(m.url);
  return `<tr class="mv-detail"><td colspan="7"><dl>
    <dt>Poste précédent</dt><dd>${esc(m.old_role)}${known(m.old_loc) ? ` — ${esc(m.old_loc)}` : ''}</dd>
    <dt>Nouveau poste</dt><dd>${esc(m.new_role)}${known(m.new_loc) ? ` — ${esc(m.new_loc)}` : ''}</dd>
    <dt>Organisation</dt><dd>${esc(m.org)} <span class="mv-sub" style="display:inline">· ${esc(m.sector)}</span></dd>
    <dt>Détail du score</dt><dd class="pts">
      <span>Changement de pays ${f(p.pays)} (${esc(m.pays)})</span>
      <span>Relocation ${f(p.reloc)} (${esc(m.reloc)})</span>
      <span>Séniorité ${f(p.sen)} (${esc(m.sen)})</span>
      <span>Secteur ${f(p.sec)} (${esc(m.sec)})</span>
      <span>Fraîcheur ${f(p.fresh)} (${esc(m.fresh)})</span></dd>
    <dt>Notes</dt><dd>${esc(m.notes)}</dd>
    <dt>Source</dt><dd>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url.replace(/^https?:\/\/(www\.)?/, '').slice(0, 80))}</a>` : 'n/d'}</dd>
  </dl></td></tr>`;
}

function renderRows() {
  const rows = MOVES.filter(matches);
  const k = state.sortKey, dir = state.sortAsc ? 1 : -1;
  rows.sort((a, b) => {
    const x = a[k], y = b[k];
    const c = (typeof x === 'number' && typeof y === 'number') ? x - y : String(x ?? '').localeCompare(String(y ?? ''), 'fr');
    return c !== 0 ? c * dir : b.score - a.score || a.person.localeCompare(b.person, 'fr');
  });

  document.getElementById('count').textContent = `${rows.length} / ${MOVES.length} mouvements`;
  document.getElementById('empty').style.display = rows.length ? 'none' : 'block';
  document.getElementById('tbody').innerHTML = rows.map(m => {
    const open = state.open.has(m.id);
    return `<tr class="mv-row${open ? ' open' : ''}" data-id="${esc(m.id)}">
      <td><div class="score"><b>${m.score}</b><span class="bar"><i class="b-${m.tier}" style="width:${Math.max(0, Math.min(100, m.score))}%"></i></span></div>
        <span class="tier tier-${m.tier}" title="${esc(TIER_HELP[m.tier])}">${m.tier}</span></td>
      <td><span class="title">${esc(m.person)}</span><span class="mv-org">${esc(m.org)}</span></td>
      <td class="hide-sm">${esc(m.old_role)}<span class="mv-sub">${esc(known(m.old_loc) ? m.old_loc : '')}</span></td>
      <td>${esc(m.new_role)}<span class="mv-sub">${esc(known(m.new_loc) ? m.new_loc : '')}</span></td>
      <td class="hide-sm">${esc(m.region)}<span class="mv-sub">${esc(m.zone)}</span></td>
      <td class="date">${dateCell(m)}</td>
      <td class="hide-sm conf-${esc(m.confidence)}">${esc(m.confidence)}</td>
    </tr>${open ? detailRow(m) : ''}`;
  }).join('');
  document.querySelectorAll('.mv-row').forEach(tr => {
    tr.onclick = e => {
      if (e.target.closest('a')) return;
      const id = tr.dataset.id;
      state.open.has(id) ? state.open.delete(id) : state.open.add(id);
      renderRows();
    };
  });
}

function renderGrid() {
  const P = GRID.points, T = GRID.tiers;
  const tbl = (title, obj) => `<div><b>${esc(title)}</b><table class="grid-table"><tbody>${
    Object.entries(obj).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${v > 0 ? '+' : ''}${v}</td></tr>`).join('')}</tbody></table></div>`;
  document.getElementById('grid-panel').innerHTML = `<div class="grid-cols">
    ${tbl('Changement de pays', P.pays)}${tbl('Relocation dans le texte', P.reloc)}
    ${tbl('Séniorité', P.sen)}${tbl('Secteur (expats)', P.sec)}${tbl('Fraîcheur', P.fresh)}
    ${tbl('Seuils de niveau (score ≥)', T)}</div>
    <p class="summary" style="margin:10px 0 0">Score = somme des cinq critères (0–100). Modifiable dans <code>site/corporate_moves.json</code> (bloc « grid »), la page recalcule tout.</p>`;
}

function refresh() { renderFilters(); renderRows(); }

async function main() {
  const res = await fetch('corporate_moves.json', {cache: 'no-store'});
  const data = await res.json();
  GRID = data.grid;
  MOVES = prepare(data.moves);
  document.getElementById('generated').textContent = data.generated || '';
  document.getElementById('scope').textContent = `${MOVES.length} mouvements · échantillon manuel`;
  document.getElementById('mv-note').textContent = data.note || '';

  document.getElementById('q').addEventListener('input', e => { state.q = e.target.value.trim(); renderRows(); });
  document.getElementById('upcoming-only').addEventListener('change', e => { state.upcomingOnly = e.target.checked; renderRows(); });
  document.getElementById('grid-toggle').onclick = () => {
    const p = document.getElementById('grid-panel');
    p.style.display = p.style.display === 'none' ? 'block' : 'none';
  };
  document.querySelectorAll('#mv-table thead th').forEach(th => {
    th.onclick = () => {
      const k = th.dataset.key;
      if (state.sortKey === k) state.sortAsc = !state.sortAsc;
      else { state.sortKey = k; state.sortAsc = (k !== 'score' && k !== 'date'); }
      renderRows();
    };
  });
  renderGrid();
  refresh();
}

main().catch(err => {
  console.error(err);
  document.getElementById('empty').style.display = 'block';
  document.getElementById('empty').textContent = 'Impossible de charger corporate_moves.json.';
});
