function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function sourceBadge(dataSource) {
  if (dataSource === 'web_scrape') return '<span class="nl-status operational" title="Site officiel">Site officiel</span>';
  if (dataSource === 'org_official') return '<span class="nl-status operational" title="Organisation internationale">Officiel</span>';
  return '';
}

function roleBadge(role) {
  if (role === 'consul') return '<span class="nl-status pipeline" title="Consul honoraire — coordonnées potentiellement personnelles">Consul honoraire</span>';
  if (role === 'diplomatic_staff') return '<span class="nl-status operational" title="Conseiller, secrétaire, attaché...">Personnel diplomatique</span>';
  return '';
}

function delegateTypeBadge(t) {
  if (t === 'observer_state') return '<span class="nl-status pipeline" title="État non-membre observateur">État observateur</span>';
  if (t === 'observer_entity') return '<span class="nl-status pipeline" title="Organisation intergouvernementale ou ONG observatrice">OIG / ONG</span>';
  return '';
}

function diplomatLine(d) {
  const since = d.start_date ? ` <span style="color:var(--ink-soft)">(depuis ${esc(d.start_date)})</span>` : '';
  const email = d.email ? ` · <a href="mailto:${esc(d.email)}">${esc(d.email)}</a>` : '';
  const phone = d.phone ? ` · ${esc(d.phone)}` : '';
  return `<div style="margin-bottom:4px">${sourceBadge(d.data_source)} ${roleBadge(d.role)} <b>${esc(d.name)}</b>${d.title ? ' — ' + esc(d.title) : ''}${since}${email}${phone}</div>`;
}

function delegateLine(d) {
  const since = d.start_date ? ` <span style="color:var(--ink-soft)">(depuis ${esc(d.start_date)})</span>` : '';
  const email = d.email ? ` · <a href="mailto:${esc(d.email)}">${esc(d.email)}</a>` : '';
  const phone = d.phone ? ` · ${esc(d.phone)}` : '';
  return `<div style="margin-bottom:4px">${delegateTypeBadge(d.delegate_type)} <b>${esc(d.name)}</b> — ${esc(d.country_source)}${d.title ? ' · ' + esc(d.title) : ''}${since}${email}${phone}</div>`;
}

let ALL_COUNTRIES = [];
let ALL_ORGS = [];

async function main() {
  const res = await fetch('contact.json', {cache: 'no-store'});
  const data = await res.json();

  document.getElementById('generated').textContent = data.generated || '';

  Object.entries(data.regions || {}).forEach(([region, countries]) => {
    countries.forEach(c => ALL_COUNTRIES.push({...c, region}));
  });

  Object.entries(data.international_organizations || {}).forEach(([org, delegates]) => {
    ALL_ORGS.push({org, delegates});
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

function renderOrgs(query) {
  const orgsEl = document.getElementById('orgs');
  if (!orgsEl) return;

  const filtered = ALL_ORGS.map(({org, delegates}) => {
    const kept = delegates.filter(d => {
      const text = `${d.name} ${d.title || ''} ${d.country_source || ''}`.toLowerCase();
      return !query || text.includes(query);
    });
    return {org, delegates: kept};
  }).filter(o => o.delegates.length);

  if (!filtered.length) {
    orgsEl.innerHTML = query ? '' : '<p class="nl-empty-region">Aucune donnée de délégation internationale pour l\'instant.</p>';
    return;
  }

  orgsEl.innerHTML = `
    <div class="region-heading">🏛️ Délégations auprès d'organisations internationales</div>
    ${filtered.map(({org, delegates}) => `
      <table class="nl-table" style="margin-bottom:18px">
        <thead><tr><th colspan="2">${esc(org)} <span style="color:var(--ink-soft);font-weight:400">(${delegates.length})</span></th></tr></thead>
        <tbody>
          ${delegates.map(d => `<tr><td colspan="2">${delegateLine(d)}</td></tr>`).join('')}
        </tbody>
      </table>`).join('')}`;
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

  const totalOrgDelegates = ALL_ORGS.reduce((n, o) => n + o.delegates.length, 0);
  document.getElementById('count').textContent =
    `${totalDiplomats} diplomate(s) bilatéraux · ${shownCountries} / ${ALL_COUNTRIES.length} pays` +
    (totalOrgDelegates ? ` · ${totalOrgDelegates} délégué(s) internationaux` : '');

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

  renderOrgs(query);
}

main().catch(err => {
  document.getElementById('regions').innerHTML =
    `<p class="nl-empty-region">Impossible de charger contact.json (${esc(err)}).</p>`;
});
