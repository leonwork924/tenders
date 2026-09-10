// Reads newsletter.json (dropped in manually after a weekly research pass)
// and renders it, with client-side filters by section ("activité") and by
// country region (via regions.json, same mapping used across the site).

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const STATUS_CLASS = {
  'signed': 'signed', 'mou': 'signed', 'pre-construction': 'signed',
  'under construction': 'construction',
  'operational': 'operational',
  'announced': 'pledge', 'pledge': 'pledge',
  'aggregate': 'pipeline', 'pipeline': 'pipeline',
  'confirmed': 'confirmed',
  'cancelled': 'cancelled',
};

function statusClass(label) {
  const l = (label || '').toLowerCase();
  for (const key in STATUS_CLASS) {
    if (l.includes(key)) return STATUS_CLASS[key];
  }
  return 'pipeline';
}

function statusBadge(label) {
  if (!label) return '';
  return `<span class="nl-status ${statusClass(label)}">${esc(label)}</span>`;
}

function sourceLink(source) {
  if (!source || !source.url) return '';
  return `<a href="${esc(source.url)}" target="_blank" rel="noopener">${esc(source.label || 'Source')}</a>`;
}

function relatedTenderBadge(rt) {
  if (!rt || !rt.title) return '';
  const q = encodeURIComponent(rt.title);
  return `<a class="related-tender" href="index.html?q=${q}" title="${esc(rt.why || '')}">
    🔗 AO lié : ${esc(rt.title.slice(0, 60))}${rt.title.length > 60 ? '…' : ''}
  </a>`;
}

function eligibilityBadge(text) {
  if (!text) return '';
  const applicable = /postulable directement|eligible/i.test(text) && !/pas postulable/i.test(text);
  const cls = applicable ? 'elig-yes' : 'elig-no';
  return `<span class="nl-elig ${cls}">${esc(text)}</span>`;
}

const ACTIVITY_SECTIONS = [
  { id: 'newsigned', label: '🆕 Deals récents' },
  { id: 'financing', label: '💶 Financements' },
  { id: 'hospitality', label: '🏨 Hôtellerie' },
  { id: 'investments', label: '🤝 Investissements' },
  { id: 'arts', label: '🎨 Arts' },
];

let DATA = null;
const state = { regions: new Set(), activities: new Set() };
let regionOf = {};

// Filet de secours : certaines valeurs de "pays" dans la newsletter sont des
// descriptions longues ("Africa (Nairobi hub; satellites in...)") plutôt
// qu'un nom de pays/région propre -- on cherche un mot-région connu dedans.
const REGION_KEYWORDS = [
  ['Afrique', 'Afrique'], ['Africa', 'Afrique'],
  ['Moyen-Orient', 'Moyen-Orient'], ['Middle East', 'Moyen-Orient'],
  ['Caraïbes', "Caraïbes / Territoires d'outre-mer"], ['Caribbean', "Caraïbes / Territoires d'outre-mer"],
  ['Amériques', 'Amériques'], ['Americas', 'Amériques'],
  ['Asie', 'Asie'], ['Asia', 'Asie'],
  ['Europe', 'Europe'],
];

function resolveRegion(pays) {
  if (!pays) return null;
  if (regionOf[pays]) return regionOf[pays];
  for (const [kw, region] of REGION_KEYWORDS) {
    if (pays.includes(kw)) return region;
  }
  return null;
}

function keep(pays) {
  if (!state.regions.size) return true;
  return state.regions.has(resolveRegion(pays));
}

function renderAll() {
  const data = DATA;

  // Deals récents, groupés par secteur
  const sectorsEl = document.getElementById('newsigned-sectors');
  const sectors = data.newly_signed || {};
  sectorsEl.innerHTML = Object.keys(sectors).map(sector => {
    const items = (sectors[sector] || []).filter(it => keep(it.pays));
    if (!items.length) return '';
    return `
    <div class="region-heading">${esc(sector)}</div>
    <table class="nl-table">
      <thead><tr><th>Date</th><th>Statut</th><th>Deal</th><th>Parties</th><th>Type</th><th>Pays</th><th>Détails</th><th>Source</th></tr></thead>
      <tbody>
        ${items.map(it => `
          <tr>
            <td>${esc(it.date_signed)}</td>
            <td>${statusBadge(it.status)}</td>
            <td><b>${esc(it.deal)}</b></td>
            <td>${esc(it.parties)}</td>
            <td>${esc(it.type)}</td>
            <td>${esc(it.pays)}</td>
            <td>${esc(it.details)}${relatedTenderBadge(it.related_tender)}</td>
            <td>${sourceLink(it.source)}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
  }).join('') || '<p class="nl-empty-region">Aucun deal identifié pour cette édition (ou filtré par la région choisie).</p>';

  // Financing & grants
  document.querySelector('#tbl-financing tbody').innerHTML = (data.financing || []).filter(it => keep(it.pays)).map(it => `
    <tr>
      <td><b>${esc(it.program)}</b></td>
      <td>${statusBadge(it.status)}</td>
      <td>${eligibilityBadge(it.eligibility)}</td>
      <td>${esc(it.org)}</td>
      <td>${esc(it.pays)}</td>
      <td class="nl-amount">${esc(it.amount)}</td>
      <td>${esc(it.deadline)}</td>
      <td>${esc(it.summary)}${relatedTenderBadge(it.related_tender)}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');

  // Hospitality, grouped by region (already geographic -- the region button filters within it too)
  const regionsEl = document.getElementById('hospitality-regions');
  const regions = data.hospitality || {};
  regionsEl.innerHTML = Object.keys(regions).map(region => {
    const items = (regions[region] || []).filter(it => keep(it.pays || region));
    return `
    <div class="region-heading">${esc(region)}</div>
    ${!items.length ? '<p class="nl-empty-region">Rien identifié pour cette région dans cette édition (ou filtré).</p>' : `
    <table class="nl-table">
      <thead><tr><th>Projet</th><th>Statut</th><th>Groupe(s)</th><th>Pays</th><th>Résumé</th><th>Contact clé</th><th>Source</th></tr></thead>
      <tbody>
        ${items.map(it => `
          <tr>
            <td><b>${esc(it.project)}</b></td>
            <td>${statusBadge(it.status)}</td>
            <td>${esc(it.group)}</td>
            <td>${esc(it.pays || region)}</td>
            <td>${esc(it.summary)}${relatedTenderBadge(it.related_tender)}</td>
            <td class="contact">${esc(it.contact)}</td>
            <td>${sourceLink(it.source)}</td>
          </tr>`).join('')}
      </tbody>
    </table>`}`;
  }).join('');

  // Investments
  document.querySelector('#tbl-investments tbody').innerHTML = (data.investments || []).filter(it => keep(it.pays)).map(it => `
    <tr>
      <td><b>${esc(it.deal)}</b></td>
      <td>${statusBadge(it.status)}</td>
      <td>${esc(it.parties)}</td>
      <td>${esc(it.type)}</td>
      <td>${esc(it.pays)}</td>
      <td class="nl-amount">${esc(it.amount)}</td>
      <td>${esc(it.scope)}${relatedTenderBadge(it.related_tender)}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');

  // Arts
  document.querySelector('#tbl-arts tbody').innerHTML = (data.arts || []).filter(it => keep(it.pays || it.region)).map(it => `
    <tr>
      <td><b>${esc(it.event)}</b></td>
      <td>${statusBadge(it.status)}</td>
      <td>${esc(it.organization)}</td>
      <td class="contact">${esc(it.people)}</td>
      <td>${esc(it.pays || it.region)}</td>
      <td>${esc(it.summary)}${relatedTenderBadge(it.related_tender)}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');
}

async function main() {
  const res = await fetch('newsletter.json', { cache: 'no-store' });
  DATA = await res.json();

  try {
    const regionsRes = await fetch('regions.json', { cache: 'no-store' });
    regionOf = await regionsRes.json();
  } catch (e) {
    console.warn('regions.json indisponible, filtre région désactivé', e);
  }

  document.getElementById('edition').textContent = DATA.edition || '';
  document.getElementById('generated').textContent = DATA.generated || '';

  // Boutons Activité -- montrent/masquent une section entière
  const activityEl = document.getElementById('activity-filter');
  ACTIVITY_SECTIONS.forEach(({ id, label }) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'filter-btn';
    btn.textContent = label;
    btn.addEventListener('click', () => {
      if (state.activities.has(id)) state.activities.delete(id); else state.activities.add(id);
      btn.classList.toggle('on');
      applyActivityFilter();
    });
    activityEl.appendChild(btn);
  });

  function applyActivityFilter() {
    ACTIVITY_SECTIONS.forEach(({ id }) => {
      const section = document.getElementById(id);
      const show = !state.activities.size || state.activities.has(id);
      section.style.display = show ? '' : 'none';
    });
  }

  // Boutons Région -- déduits des pays réellement présents dans l'édition
  const paysPresent = new Set();
  const collectPays = it => it.pays && paysPresent.add(it.pays);
  Object.values(DATA.newly_signed || {}).forEach(arr => arr.forEach(collectPays));
  (DATA.financing || []).forEach(collectPays);
  Object.entries(DATA.hospitality || {}).forEach(([region, arr]) => arr.forEach(it => paysPresent.add(it.pays || region)));
  (DATA.investments || []).forEach(collectPays);
  (DATA.arts || []).forEach(it => paysPresent.add(it.pays || it.region));

  const regionsPresent = new Set();
  paysPresent.forEach(p => { const r = resolveRegion(p); if (r) regionsPresent.add(r); });
  const regionEl = document.getElementById('region-filter');
  Array.from(regionsPresent).sort().forEach(r => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'filter-btn';
    btn.textContent = r;
    btn.addEventListener('click', () => {
      if (state.regions.has(r)) state.regions.delete(r); else state.regions.add(r);
      btn.classList.toggle('on');
      renderAll();
    });
    regionEl.appendChild(btn);
  });

  renderAll();
}

main().catch(err => {
  document.querySelector('.nl-main').innerHTML =
    `<p class="empty">Impossible de charger newsletter.json (${esc(err)}).</p>`;
});
