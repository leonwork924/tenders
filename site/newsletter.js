// Reads newsletter.json (dropped in manually after a weekly research pass)
// and renders it. No filtering/search here -- it's a short weekly read,
// not a database to query like the main tenders page.

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

// --- Related-tender matching -----------------------------------------
// Best-effort, done client-side so it's always checked against whatever
// tenders are live right now, regardless of when the newsletter itself was
// last refreshed. Heuristic: same country mentioned in the item's text,
// tie-broken by shared significant words with the tender's title/buyer.
const STOPWORDS = new Set(['the','and','for','with','from','into','over','under','this','that',
  'des','les','pour','dans','avec','vers','entre','sur','par','une','un','du','de','la','le',
  'aux','and','of','to','in','on','at','by','new','world','group','million','billion']);

function tokens(text) {
  return (text || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .split(/[^a-z0-9]+/).filter(w => w.length >= 4 && !STOPWORDS.has(w));
}

function buildMatcher(tenderItems) {
  const withMeta = (tenderItems || []).map(t => ({
    tender: t,
    countryName: (t.country_name || '').toLowerCase(),
    titleTokens: new Set(tokens((t.title || '') + ' ' + (t.buyer || ''))),
  })).filter(t => t.countryName);

  return function relatedTender(itemText) {
    const text = (itemText || '').toLowerCase();
    if (!text) return null;
    const candidates = withMeta.filter(t => t.countryName.length > 3 && text.includes(t.countryName));
    if (!candidates.length) return null;
    const itemTokens = tokens(itemText);
    let best = null, bestScore = -1;
    for (const c of candidates) {
      const overlap = itemTokens.filter(w => c.titleTokens.has(w)).length;
      const score = overlap * 10 + c.tender.score; // word overlap dominates, tender score breaks ties
      if (score > bestScore) { bestScore = score; best = c.tender; }
    }
    return best;
  };
}

function relatedBadge(tender) {
  if (!tender) return '';
  const q = encodeURIComponent((tender.title || '').split(' ').slice(0, 4).join(' '));
  return `<a class="related-tender" href="index.html?q=${q}" title="Ouvre le tender correspondant sur la page Tenders">
    🔗 AO lié : ${esc((tender.title || '').slice(0, 60))}${(tender.title || '').length > 60 ? '…' : ''}
  </a>`;
}

async function main() {
  const [nlRes, tendersRes] = await Promise.all([
    fetch('newsletter.json', { cache: 'no-store' }),
    fetch('data.json', { cache: 'no-store' }).catch(() => null),
  ]);
  const data = await nlRes.json();
  let relatedTender = () => null;
  if (tendersRes && tendersRes.ok) {
    try {
      const tenderData = await tendersRes.json();
      relatedTender = buildMatcher(tenderData.items);
    } catch (e) { /* tenders unavailable -- newsletter still works without the links */ }
  }

  document.getElementById('edition').textContent = data.edition || '';
  document.getElementById('generated').textContent = data.generated || '';

  // Newly signed
  document.querySelector('#tbl-newsigned tbody').innerHTML = (data.newly_signed || []).map(it => `
    <tr>
      <td>${esc(it.date_signed)}</td>
      <td>${statusBadge(it.status)}</td>
      <td><b>${esc(it.deal)}</b></td>
      <td>${esc(it.parties)}</td>
      <td>${esc(it.type)}</td>
      <td>${esc(it.details)}${relatedBadge(relatedTender(it.deal + ' ' + it.parties + ' ' + it.details))}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');

  // Hospitality, grouped by region
  const regionsEl = document.getElementById('hospitality-regions');
  const regions = data.hospitality || {};
  regionsEl.innerHTML = Object.keys(regions).map(region => `
    <div class="region-heading">${esc(region)}</div>
    ${!regions[region].length ? '<p class="nl-empty-region">Rien identifie pour cette region dans cette edition.</p>' : `
    <table class="nl-table">
      <thead><tr><th>Projet</th><th>Statut</th><th>Groupe(s)</th><th>Résumé</th><th>Contact clé</th><th>Source</th></tr></thead>
      <tbody>
        ${regions[region].map(it => `
          <tr>
            <td><b>${esc(it.project)}</b></td>
            <td>${statusBadge(it.status)}</td>
            <td>${esc(it.group)}</td>
            <td>${esc(it.summary)}${relatedBadge(relatedTender(it.project + ' ' + it.summary + ' ' + region))}</td>
            <td class="contact">${esc(it.contact)}</td>
            <td>${sourceLink(it.source)}</td>
          </tr>`).join('')}
      </tbody>
    </table>`}`).join('');

  // Investments
  document.querySelector('#tbl-investments tbody').innerHTML = (data.investments || []).map(it => `
    <tr>
      <td><b>${esc(it.deal)}</b></td>
      <td>${statusBadge(it.status)}</td>
      <td>${esc(it.parties)}</td>
      <td>${esc(it.type)}</td>
      <td class="nl-amount">${esc(it.amount)}</td>
      <td>${esc(it.scope)}${relatedBadge(relatedTender(it.deal + ' ' + it.parties + ' ' + it.scope))}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');

  // Arts
  document.querySelector('#tbl-arts tbody').innerHTML = (data.arts || []).map(it => `
    <tr>
      <td><b>${esc(it.event)}</b></td>
      <td>${statusBadge(it.status)}</td>
      <td>${esc(it.organization)}</td>
      <td class="contact">${esc(it.people)}</td>
      <td>${esc(it.region)}</td>
      <td>${esc(it.summary)}${relatedBadge(relatedTender(it.event + ' ' + it.summary + ' ' + it.region))}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');
}

main().catch(err => {
  document.querySelector('.nl-main').innerHTML =
    `<p class="empty">Impossible de charger newsletter.json (${esc(err)}).</p>`;
});
