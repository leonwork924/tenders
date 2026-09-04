function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function sourceBadge(dataSource) {
  if (dataSource === 'wikidata') return '<span class="nl-status confirmed" title="Wikidata (CC0)">Wikidata</span>';
  if (dataSource === 'web_scrape') return '<span class="nl-status operational" title="Site officiel">Site officiel</span>';
  return '';
}

function diplomatLine(d) {
  const since = d.start_date ? ` <span style="color:var(--ink-soft)">(depuis ${esc(d.start_date)})</span>` : '';
  const wd = d.wikidata_url ? ` · <a href="${esc(d.wikidata_url)}" target="_blank" rel="noopener">wikidata</a>` : '';
  const email = d.email ? ` · <a href="mailto:${esc(d.email)}">${esc(d.email)}</a>` : '';
  const phone = d.phone ? ` · ${esc(d.phone)}` : '';
  return `<div style="margin-bottom:4px">${sourceBadge(d.data_source)} <b>${esc(d.name)}</b>${d.title ? ' — ' + esc(d.title) : ''}${since}${wd}${email}${phone}</div>`;
}

let ALL_COUNTRIES = [];

async function main() {
  const res = await fetch('contact.json', {cache: 'no-store'});
  const data = await res.json();

  document.getElementById('generated').textContent = data.generated || '';

  Object.entries(data.regions || {}).forEach(([region, countries]) => {
    countries.forEach(c => ALL_COUNTRIES.push({...c, region}));
  });

  document.getElementById('methodo-text').textContent = data.methodology || '';
  document.getElementById('notes-list').innerHTML = (data.notes || []).map(n => `<li>${esc(n)}</li>`).join('');
  document.getElementById('search-terms').innerHTML = (data.search_terms || [])
    .map(t => `<span class="term-chip">${esc(t)}</span>`).join('');
  document.getElementById('methodo-toggle').addEventListener('click', () => {
    const body = document.getElementById('methodo-body');
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    document.getElementById('methodo-toggle').textContent = (open ? '▸' : '▾') + ' Méthodologie & termes de recherche';
  });

  document.getElementById('q').addEventListener('input', e => render(e.target.value.toLowerCase()));
  render('');
}

function render(query) {
  const regionsEl = document.getElementById('regions');
  const byRegion = {};
  let shownCountries = 0, totalDiplomats = 0;

  ALL_COUNTRIES.forEach(c => {
    const names = (c.diplomats || []).map(d => `${d.name} ${d.title || ''} ${d.email || ''}`).join(' ');
    const text = (c.country + ' ' + names).toLowerCase();
    if (query && !text.includes(query)) return;
    (byRegion[c.region] = byRegion[c.region] || []).push(c);
    shownCountries++;
    totalDiplomats += (c.diplomats || []).length;
  });

  document.getElementById('count').textContent = `${totalDiplomats} diplomate(s) · ${shownCountries} / ${ALL_COUNTRIES.length} pays`;

  regionsEl.innerHTML = Object.keys(byRegion).map(region => `
    <div class="region-heading">${esc(region)} <span style="color:var(--ink-soft);font-weight:400">(${byRegion[region].length})</span></div>
    <table class="nl-table">
      <thead><tr><th>Pays</th><th>Diplomate(s)</th><th>Source officielle</th></tr></thead>
      <tbody>
        ${byRegion[region].map(c => `
          <tr>
            <td><b>${esc(c.country)}</b></td>
            <td>${(c.diplomats && c.diplomats.length)
                ? c.diplomats.map(diplomatLine).join('')
                : '<span class="nl-status pipeline">aucune donnée extraite</span>'}</td>
            <td><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.url.replace(/^https?:\/\//, '').split('/')[0])}</a></td>
          </tr>`).join('')}
      </tbody>
    </table>`).join('') || '<p class="nl-empty-region">Aucun résultat pour ce filtre.</p>';
}

main().catch(err => {
  document.getElementById('regions').innerHTML =
    `<p class="nl-empty-region">Impossible de charger contact.json (${esc(err)}).</p>`;
});
