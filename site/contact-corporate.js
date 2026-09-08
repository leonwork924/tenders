function escC(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

document.querySelectorAll('#contact-subtabs a[data-tab]').forEach(a => {
  a.addEventListener('click', e => {
    e.preventDefault();
    document.querySelectorAll('#contact-subtabs a[data-tab]').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
    const tab = a.dataset.tab;
    document.getElementById('tab-diplo').style.display = tab === 'diplo' ? '' : 'none';
    document.getElementById('tab-corporate').style.display = tab === 'corporate' ? '' : 'none';
    if (tab === 'corporate') loadCorporateStats();
  });
});

let corpLoaded = false;

async function loadCorporateStats() {
  if (corpLoaded) return;
  corpLoaded = true;
  const el = document.getElementById('corp-stats');
  try {
    const res = await fetch('corporate_contacts.json', {cache: 'no-store'});
    const data = await res.json();

    const lastRun = data.last_run
      ? `${escC(data.last_run.status)} · ${(data.last_run.records_seen || 0).toLocaleString('fr-FR')} lignes parcourues, ${(data.last_run.records_imported || 0).toLocaleString('fr-FR')} nouvelles`
      : 'aucun run enregistré';

    const jurisRows = (data.by_jurisdiction || []).map(j =>
      `<tr><td>${escC((j.jurisdiction || '').toUpperCase())}</td><td>${j.count.toLocaleString('fr-FR')}</td></tr>`
    ).join('');

    const sourceRows = (data.by_source || []).map(s =>
      `<tr><td>${escC(s.source)}</td><td>${s.count.toLocaleString('fr-FR')}</td></tr>`
    ).join('');

    const sampleRows = (data.sample || []).map(c => `
      <tr>
        <td><b>${escC(c.legal_name)}</b></td>
        <td>${escC((c.jurisdiction || '').toUpperCase())}</td>
        <td>${escC(c.hq_city)}${c.hq_city && c.hq_country ? ', ' : ''}${escC(c.hq_country)}</td>
        <td>${c.lei ? `<code>${escC(c.lei)}</code>` : ''}</td>
      </tr>`).join('');

    el.innerHTML = `
      <div class="region-heading">Vue d'ensemble <span style="color:var(--ink-soft);font-weight:400">(généré le ${escC(data.generated)})</span></div>
      <p class="nl-sub">${(data.total_companies || 0).toLocaleString('fr-FR')} entreprise(s)/entité(s) au total &nbsp;·&nbsp; dernier run : ${lastRun}</p>

      <div class="region-heading">Par juridiction</div>
      <table class="nl-table">
        <thead><tr><th>Pays</th><th>Nb d'entités</th></tr></thead>
        <tbody>${jurisRows || '<tr><td colspan="2">Aucune donnée pour l\'instant</td></tr>'}</tbody>
      </table>

      <div class="region-heading">Par source</div>
      <table class="nl-table">
        <thead><tr><th>Source</th><th>Nb d'entités</th></tr></thead>
        <tbody>${sourceRows || '<tr><td colspan="2">Aucune donnée pour l\'instant</td></tr>'}</tbody>
      </table>

      <div class="region-heading">Échantillon (40 au hasard, pas la base complète)</div>
      <table class="nl-table">
        <thead><tr><th>Entité</th><th>Pays</th><th>Ville / Pays du siège</th><th>LEI</th></tr></thead>
        <tbody>${sampleRows || '<tr><td colspan="4">Aucune donnée pour l\'instant</td></tr>'}</tbody>
      </table>`;
  } catch (err) {
    el.innerHTML = `<p class="nl-empty-region">Impossible de charger corporate_contacts.json (${escC(err)}). Le premier run n'a peut-être pas encore eu lieu.</p>`;
  }
}
