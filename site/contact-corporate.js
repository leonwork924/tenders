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
let CORP_INDEX = [];

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

    const sourceRows = (data.by_source || []).map(s =>
      `<tr><td>${escC(s.source)}</td><td>${s.count.toLocaleString('fr-FR')}</td></tr>`
    ).join('');

    el.innerHTML = `
      <div class="region-heading">Vue d'ensemble <span style="color:var(--ink-soft);font-weight:400">(généré le ${escC(data.generated)})</span></div>
      <p class="nl-sub">${(data.total_companies || 0).toLocaleString('fr-FR')} entreprise(s)/entité(s) au total &nbsp;·&nbsp; dernier run : ${lastRun}</p>

      <div class="region-heading">Par source</div>
      <table class="nl-table">
        <thead><tr><th>Source</th><th>Nb d'entités</th></tr></thead>
        <tbody>${sourceRows || '<tr><td colspan="2">Aucune donnée pour l\'instant</td></tr>'}</tbody>
      </table>

      <div class="region-heading">Liste complète, par pays</div>
      <div class="toolbar" style="margin-bottom:10px">
        <label for="corp-country-select">Pays</label>
        <select id="corp-country-select" style="flex:1;max-width:320px"><option value="">— choisir un pays —</option></select>
      </div>
      <div id="corp-country-list"></div>`;

    document.getElementById('corp-country-select').addEventListener('change', e => {
      if (e.target.value) loadCountryPage(e.target.value, 0);
    });

    await loadCorpIndex();
  } catch (err) {
    el.innerHTML = `<p class="nl-empty-region">Impossible de charger corporate_contacts.json (${escC(err)}). Le premier run n'a peut-être pas encore eu lieu.</p>`;
  }
}

async function loadCorpIndex() {
  const select = document.getElementById('corp-country-select');
  try {
    const res = await fetch('corporate/index.json', {cache: 'no-store'});
    const data = await res.json();
    CORP_INDEX = data.jurisdictions || [];
    CORP_INDEX
      .slice()
      .sort((a, b) => b.count - a.count)
      .forEach(j => {
        const opt = document.createElement('option');
        opt.value = j.jurisdiction;
        opt.textContent = `${j.jurisdiction.toUpperCase()} (${j.count.toLocaleString('fr-FR')})`;
        select.appendChild(opt);
      });
  } catch (err) {
    select.insertAdjacentHTML('afterend', `<p class="nl-empty-region">Liste par pays pas encore disponible (${escC(err)}).</p>`);
  }
}

async function loadCountryPage(jurisdiction, page) {
  const listEl = document.getElementById('corp-country-list');
  listEl.innerHTML = '<p class="nl-sub">Chargement…</p>';
  const info = CORP_INDEX.find(j => j.jurisdiction === jurisdiction);
  try {
    const res = await fetch(`corporate/${jurisdiction}_${page}.json`, {cache: 'no-store'});
    const data = await res.json();
    const rows = (data.companies || []).map(c => `
      <tr>
        <td><b>${escC(c.n)}</b></td>
        <td>${escC(c.city)}${c.city && (c.state || c.country) ? ', ' : ''}${escC(c.state)} ${escC(c.country)}</td>
        <td>${c.lei ? `<code>${escC(c.lei)}</code>` : ''}</td>
        <td>${escC(c.reg)}</td>
      </tr>`).join('');

    const totalPages = info ? info.pages : 1;
    const pager = totalPages > 1 ? `
      <div class="toolbar" style="margin-top:10px">
        <button class="btn-toggle" ${page <= 0 ? 'disabled' : ''} data-nav="prev">‹ précédent</button>
        <span class="count">page ${page + 1} / ${totalPages}</span>
        <button class="btn-toggle" ${page >= totalPages - 1 ? 'disabled' : ''} data-nav="next">suivant ›</button>
      </div>` : '';

    listEl.innerHTML = `
      <p class="nl-sub">${jurisdiction.toUpperCase()} — ${(info ? info.count : data.companies.length).toLocaleString('fr-FR')} entreprise(s)</p>
      <table class="nl-table">
        <thead><tr><th>Nom</th><th>Ville / Région / Pays</th><th>LEI</th><th>N° registre</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="4">Aucune entreprise</td></tr>'}</tbody>
      </table>
      ${pager}`;

    listEl.querySelector('[data-nav="prev"]')?.addEventListener('click', () => loadCountryPage(jurisdiction, page - 1));
    listEl.querySelector('[data-nav="next"]')?.addEventListener('click', () => loadCountryPage(jurisdiction, page + 1));
  } catch (err) {
    listEl.innerHTML = `<p class="nl-empty-region">Impossible de charger ce pays (${escC(err)}).</p>`;
  }
}
