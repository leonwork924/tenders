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

async function main() {
  const res = await fetch('newsletter.json', { cache: 'no-store' });
  const data = await res.json();

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
      <td>${esc(it.details)}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');

  // Hospitality, grouped by region
  const regionsEl = document.getElementById('hospitality-regions');
  const regions = data.hospitality || {};
  regionsEl.innerHTML = Object.keys(regions).map(region => `
    <div class="region-heading">${esc(region)}</div>
    <table class="nl-table">
      <thead><tr><th>Projet</th><th>Statut</th><th>Groupe(s)</th><th>Résumé</th><th>Contact clé</th><th>Source</th></tr></thead>
      <tbody>
        ${regions[region].map(it => `
          <tr>
            <td><b>${esc(it.project)}</b></td>
            <td>${statusBadge(it.status)}</td>
            <td>${esc(it.group)}</td>
            <td>${esc(it.summary)}</td>
            <td class="contact">${esc(it.contact)}</td>
            <td>${sourceLink(it.source)}</td>
          </tr>`).join('')}
      </tbody>
    </table>`).join('');

  // Investments
  document.querySelector('#tbl-investments tbody').innerHTML = (data.investments || []).map(it => `
    <tr>
      <td><b>${esc(it.deal)}</b></td>
      <td>${statusBadge(it.status)}</td>
      <td>${esc(it.parties)}</td>
      <td>${esc(it.type)}</td>
      <td class="nl-amount">${esc(it.amount)}</td>
      <td>${esc(it.scope)}</td>
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
      <td>${esc(it.summary)}</td>
      <td>${sourceLink(it.source)}</td>
    </tr>`).join('');
}

main().catch(err => {
  document.querySelector('.nl-main').innerHTML =
    `<p class="empty">Impossible de charger newsletter.json (${esc(err)}).</p>`;
});
