function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function statusBadge(status) {
  if (status === 'verified') return '<span class="nl-status confirmed">✓ Vérifié</span>';
  if (status === 'warning') return '<span class="nl-status cancelled">⚠ Attention</span>';
  return '<span class="nl-status pipeline">non re-testé</span>';
}

let ALL_ITEMS = [];

async function main() {
  const res = await fetch('contact.json', {cache: 'no-store'});
  const data = await res.json();

  document.getElementById('generated').textContent = data.generated || '';

  Object.entries(data.regions || {}).forEach(([region, items]) => {
    items.forEach(it => ALL_ITEMS.push({...it, region}));
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
  let shown = 0;
  ALL_ITEMS.forEach(it => {
    const text = (it.country + ' ' + it.org + ' ' + (it.note || '')).toLowerCase();
    if (query && !text.includes(query)) return;
    (byRegion[it.region] = byRegion[it.region] || []).push(it);
    shown++;
  });

  document.getElementById('count').textContent = `${shown} / ${ALL_ITEMS.length}`;

  regionsEl.innerHTML = Object.keys(byRegion).map(region => `
    <div class="region-heading">${esc(region)} <span style="color:var(--ink-soft);font-weight:400">(${byRegion[region].length})</span></div>
    <table class="nl-table">
      <thead><tr><th>Pays / entité</th><th>Organisme</th><th>Statut</th><th>Lien</th><th>Note</th></tr></thead>
      <tbody>
        ${byRegion[region].map(it => `
          <tr>
            <td><b>${esc(it.country)}</b></td>
            <td>${esc(it.org)}</td>
            <td>${statusBadge(it.status)}</td>
            <td><a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.url.replace(/^https?:\/\//, '').split('/')[0])}</a></td>
            <td class="contact">${esc(it.note || '')}</td>
          </tr>`).join('')}
      </tbody>
    </table>`).join('') || '<p class="nl-empty-region">Aucun résultat pour ce filtre.</p>';
}

main().catch(err => {
  document.getElementById('regions').innerHTML =
    `<p class="nl-empty-region">Impossible de charger contact.json (${esc(err)}).</p>`;
});
