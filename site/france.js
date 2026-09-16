function escF(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

let FR_AMBASSADORS = [];

async function loadFrance() {
  const el = document.getElementById('france-main');
  try {
    const res = await fetch('france_ambassadeurs.json', { cache: 'no-store' });
    const data = await res.json();
    FR_AMBASSADORS = data.ambassadors || [];

    document.getElementById('france-generated').textContent = data.generated ? new Date(data.generated).toLocaleDateString('fr-FR') : '';
    document.getElementById('france-review-count').textContent = data.needs_review_count
      ? `${data.needs_review_count} lettre(s) à relire avant envoi (pays hors table de prépositions)`
      : '';

    el.innerHTML = `
      <div class="toolbar">
        <label for="france-q">Filtre</label>
        <input id="france-q" type="search" placeholder="nom, pays…" autocomplete="off">
        <span class="count" id="france-count"></span>
      </div>
      <div id="france-list"></div>`;

    document.getElementById('france-q').addEventListener('input', e => renderFrance(e.target.value.toLowerCase()));
    renderFrance('');
  } catch (err) {
    el.innerHTML = `<p class="nl-empty-region">Impossible de charger france_ambassadeurs.json (${escF(err)}). Le workflow "france (nominations JORF)" n'a peut-être pas encore tourné.</p>`;
  }
}

function ambassadorCard(a, idx) {
  const reviewBadge = a.needs_review
    ? '<span class="nl-status pipeline" title="Pays absent de la table de prépositions -- vérifier la lettre avant envoi">à relire</span>'
    : '<span class="nl-status operational">prête</span>';

  return `
    <div class="nl-table" style="margin-bottom:14px;padding:14px 16px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:8px">
        <div><b>${escF(a.name)}</b> — ${escF(a.country)} ${reviewBadge}</div>
        <div style="color:var(--ink-soft);font-size:13px">
          Nommé(e) le ${escF(a.nomination_date)} · <a href="${escF(a.jorf_url)}" target="_blank" rel="noopener">JORF</a>
        </div>
      </div>
      <details>
        <summary style="cursor:pointer;color:var(--ink-soft);font-size:13px">Voir / copier la lettre</summary>
        <textarea readonly style="width:100%;min-height:220px;margin-top:8px;font-family:inherit;font-size:13px;padding:8px" id="letter-${idx}">${escF(a.letter)}</textarea>
        <button type="button" style="margin-top:6px" onclick="copyLetter(${idx})">Copier</button>
      </details>
    </div>`;
}

function copyLetter(idx) {
  const el = document.getElementById(`letter-${idx}`);
  el.select();
  navigator.clipboard?.writeText(el.value);
}

function renderFrance(query) {
  const list = document.getElementById('france-list');
  const filtered = FR_AMBASSADORS.filter(a => {
    if (!query) return true;
    return `${a.name} ${a.country}`.toLowerCase().includes(query);
  });

  document.getElementById('france-count').textContent = `${filtered.length} / ${FR_AMBASSADORS.length} nomination(s)`;
  list.innerHTML = filtered.map(ambassadorCard).join('') ||
    `<p class="nl-empty-region">${FR_AMBASSADORS.length ? 'Aucun résultat pour ce filtre.' : 'Aucune nomination pour l\'instant.'}</p>`;
}
