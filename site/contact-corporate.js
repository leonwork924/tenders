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
    document.getElementById('tab-france').style.display = tab === 'france' ? '' : 'none';
    if (tab === 'corporate') loadCorporate();
    if (tab === 'france') loadFrance();
  });
});

let CORP_COMPANIES = [];
let corpLoaded = false;

function contactLine(c) {
  const email = c.email ? ` · <a href="mailto:${escC(c.email)}">${escC(c.email)}</a>` : '';
  const phone = c.phone ? ` · ${escC(c.phone)}` : '';
  const status = c.email_verification_status === 'valid'
    ? ' <span class="nl-status operational" title="Email vérifié">vérifié</span>'
    : '';
  return `<div style="margin-bottom:4px"><b>${escC(c.full_name)}</b>${c.job_title ? ' — ' + escC(c.job_title) : ''}${email}${phone}${status}</div>`;
}

function companyRow(co) {
  const loc = [co.hq_city, co.hq_state, co.hq_country].filter(Boolean).join(', ');
  const registryContact = [co.registry_phone, co.registry_email].filter(Boolean).join(' · ');
  return `
    <tr>
      <td>
        <b>${escC(co.legal_name)}</b>
        <div style="color:var(--ink-soft);font-size:12px">
          ${escC((co.jurisdiction || '').toUpperCase())}${co.company_number ? ' · n° ' + escC(co.company_number) : ''}${co.lei ? ' · LEI ' + escC(co.lei) : ''}
        </div>
      </td>
      <td>${co.contacts && co.contacts.length ? co.contacts.map(contactLine).join('') : (registryContact || '<span class="nl-status pipeline">aucun contact</span>')}</td>
      <td>${loc || ''}</td>
      <td><a href="${escC(co.source_url)}" target="_blank" rel="noopener">${escC(co.source_name)}</a></td>
    </tr>`;
}

async function loadCorporate() {
  if (corpLoaded) return;
  corpLoaded = true;
  const el = document.getElementById('corp-main');
  try {
    const res = await fetch('corporate_contacts.json', { cache: 'no-store' });
    const data = await res.json();
    CORP_COMPANIES = data.companies || [];

    document.getElementById('corp-generated').textContent = data.generated ? new Date(data.generated).toLocaleDateString('fr-FR') : '';

    el.innerHTML = `
      <div class="toolbar">
        <label for="corp-q">Filtre</label>
        <input id="corp-q" type="search" placeholder="entreprise, contact, pays…" autocomplete="off">
        <span class="count" id="corp-count"></span>
      </div>
      <table class="nl-table">
        <thead><tr><th>Entreprise</th><th>Contact(s)</th><th>Siège</th><th>Source</th></tr></thead>
        <tbody id="corp-tbody"></tbody>
      </table>`;

    document.getElementById('corp-q').addEventListener('input', e => renderCorp(e.target.value.toLowerCase()));
    renderCorp('');
  } catch (err) {
    el.innerHTML = `<p class="nl-empty-region">Impossible de charger corporate_contacts.json (${escC(err)}). Lance le workflow "corporate contacts (lookup)" depuis l'onglet Actions pour rechercher une première entreprise.</p>`;
  }
}

function renderCorp(query) {
  const tbody = document.getElementById('corp-tbody');
  const filtered = CORP_COMPANIES.filter(co => {
    if (!query) return true;
    const names = (co.contacts || []).map(c => `${c.full_name} ${c.job_title || ''} ${c.email || ''}`).join(' ');
    const text = `${co.legal_name} ${co.jurisdiction || ''} ${co.hq_city || ''} ${co.hq_country || ''} ${names}`.toLowerCase();
    return text.includes(query);
  });

  document.getElementById('corp-count').textContent = `${filtered.length} / ${CORP_COMPANIES.length} entreprise(s)`;
  tbody.innerHTML = filtered.map(companyRow).join('') ||
    `<tr><td colspan="4" class="nl-empty-region">${CORP_COMPANIES.length ? 'Aucun résultat pour ce filtre.' : 'Aucune entreprise recherchée pour l\'instant.'}</td></tr>`;
}
