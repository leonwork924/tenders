// No build step, no framework, no login: fetch data.json (written by the
// fetch pipeline and committed by GitHub Actions) and render it. Re-deploys
// on every push, so this file just has to render whatever is in data.json
// today.

const state = { q: '', src: '', country: '', urgentOnly: false, sortKey: 'score', sortAsc: false };

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function urgency(days) {
  if (days === '' || days === null || days === undefined) return 'ok';
  const d = parseInt(days, 10);
  if (d <= 7) return 'soon';
  if (d <= 21) return 'mid';
  return 'ok';
}

function money(value, currency) {
  if (value === null || value === undefined || value === '') return '';
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  const rendered = Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, {maximumFractionDigits: 2});
  return `${rendered} ${currency || ''}`.trim();
}

async function main() {
  const res = await fetch('data.json', {cache: 'no-store'});
  const data = await res.json();

  document.getElementById('generated').textContent = data.generated || '';
  document.getElementById('scope').textContent = data.scope || '';

  const sourceSet = new Set((data.items || []).map(it => it.source).filter(Boolean));
  const srcSel = document.getElementById('src');
  Array.from(sourceSet).sort().forEach(s => {
    const opt = document.createElement('option');
    opt.value = s; opt.textContent = s;
    srcSel.appendChild(opt);
  });

  const countryNames = new Map();
  (data.items || []).forEach(it => {
    if (it.country) countryNames.set(it.country, it.country_name || it.country);
  });
  const countrySel = document.getElementById('country');
  Array.from(countryNames.entries())
    .sort((a, b) => a[1].localeCompare(b[1]))
    .forEach(([code, name]) => {
      const opt = document.createElement('option');
      opt.value = code; opt.textContent = name;
      countrySel.appendChild(opt);
    });
  const initialCountry = new URLSearchParams(location.search).get('country');
  if (initialCountry && countryNames.has(initialCountry)) {
    state.country = initialCountry;
    countrySel.value = initialCountry;
  }
  const initialQuery = new URLSearchParams(location.search).get('q');
  if (initialQuery) {
    state.q = initialQuery.toLowerCase();
    document.getElementById('q').value = initialQuery;
  }

  document.getElementById('q').addEventListener('input', e => { state.q = e.target.value.toLowerCase(); refresh(); });
  srcSel.addEventListener('change', e => { state.src = e.target.value; refresh(); });
  countrySel.addEventListener('change', e => { state.country = e.target.value; refresh(); });
  document.getElementById('urgent-only').addEventListener('change', e => { state.urgentOnly = e.target.checked; refresh(); });
  document.querySelectorAll('thead th[data-key]').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.key;
      state.sortAsc = state.sortKey === key ? !state.sortAsc : false;
      state.sortKey = key;
      refresh();
    });
  });

  // Source health panel -- built once, toggled on demand.
  const healthPanel = document.getElementById('health-panel');
  const healthTbody = document.getElementById('health-tbody');
  const health = (data.source_health || []).slice().sort((a, b) => a.source.localeCompare(b.source));
  healthTbody.innerHTML = health.map(s => `
    <tr>
      <td>${esc(s.source)}</td>
      <td>${esc((s.last_run || '').replace('T', ' ').slice(0, 16))}</td>
      <td class="${s.ok ? 'ok' : 'soon'}">${s.ok ? 'OK' : (s.last_run ? 'echec' : 'jamais lance')}</td>
      <td class="num">${s.active_count}</td>
    </tr>`).join('') || '<tr><td colspan="4">Pas encore de donnees.</td></tr>';
  document.getElementById('health-toggle').addEventListener('click', () => {
    healthPanel.style.display = healthPanel.style.display === 'none' ? 'block' : 'none';
  });

  function refresh() {
    let items = data.items || [];
    if (state.country) items = items.filter(it => it.country === state.country);
    if (state.src) items = items.filter(it => it.source === state.src);
    if (state.urgentOnly) items = items.filter(it => it.days_left !== '' && it.days_left !== null && parseInt(it.days_left, 10) <= 7);
    if (state.q) {
      items = items.filter(it => [it.title, it.buyer, it.country_name, it.matched, it.source]
        .join(' ').toLowerCase().includes(state.q));
    }
    items = items.slice().sort((a, b) => {
      let av = a[state.sortKey], bv = b[state.sortKey];
      if (state.sortKey === 'deadline') { av = av || '9999-12-31'; bv = bv || '9999-12-31'; }
      const an = parseFloat(av), bn = parseFloat(bv);
      const numeric = state.sortKey === 'score' || state.sortKey === 'value';
      const cmp = numeric ? (an - bn) : String(av ?? '').localeCompare(String(bv ?? ''));
      return state.sortAsc ? cmp : -cmp;
    });
    render(items);
  }

  function render(items) {
    const maxScore = Math.max(1, ...items.map(it => it.score || 0));
    const tbody = document.getElementById('tbody');
    const empty = document.getElementById('empty');
    document.getElementById('count').textContent = `${items.length} / ${data.items.length}`;

    if (!items.length) {
      tbody.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    tbody.innerHTML = items.map(it => `
      <tr>
        <td class="num">
          <span class="score"><b>${(it.score || 0).toFixed(0)}</b>
          <span class="bar"><i style="width:${Math.min(100, (it.score || 0) / maxScore * 100).toFixed(0)}%"></i></span></span>
        </td>
        <td>
          <a class="title" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>
          ${it.is_new ? '<span class="new-badge">Nouveau</span>' : ''}
          <span class="why">${esc((it.matched || '').slice(0, 180))}</span>
        </td>
        <td class="hide-sm">${esc(it.buyer)}</td>
        <td>${esc(it.country_name || it.country)}</td>
        <td class="date">
          ${esc((it.deadline || '').slice(0, 10))}
          ${it.days_left !== '' && it.days_left !== null ? `<span class="chip ${urgency(it.days_left)}">${esc(it.days_left)}j</span>` : ''}
        </td>
        <td class="num hide-sm">${esc(money(it.value, it.currency))}</td>
        <td class="src hide-sm"><span class="src-chip">${esc(it.source)}</span></td>
      </tr>`).join('');
  }

  refresh();
  loadHistory();
}

// --- Historique / renouvellements -------------------------------------
function showSubtab(which) {
  document.getElementById('subtab-active').classList.toggle('on', which === 'active');
  document.getElementById('subtab-history').classList.toggle('on', which === 'history');
  document.getElementById('view-active').style.display = which === 'active' ? '' : 'none';
  document.getElementById('view-history').style.display = which === 'history' ? '' : 'none';
  document.getElementById('active-toolbar').style.display = which === 'active' ? '' : 'none';
  const panel = document.getElementById('health-panel');
  if (which === 'history' && panel) panel.style.display = 'none';
}

async function loadHistory() {
  let hist;
  try {
    const res = await fetch('history.json', {cache: 'no-store'});
    hist = await res.json();
  } catch (e) {
    document.getElementById('history-empty').style.display = 'block';
    document.getElementById('history-empty').textContent = "Impossible de charger history.json.";
    return;
  }

  const alertBox = document.getElementById('renewal-alert');
  if ((hist.renewals || []).length) {
    alertBox.style.display = 'block';
    alertBox.innerHTML = `<b>⏰ ${hist.renewals.length} contrat(s) arrivent à échéance dans les 6 prochains mois</b>
      — le marché revient probablement en jeu, à surveiller pour repostuler :
      <ul>${hist.renewals.map(r => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>
        — ${esc(r.country_name || r.country)} — fin de contrat estimée ${esc((r.contract_end || '').slice(0,10))}</li>`).join('')}</ul>`;
  } else {
    alertBox.style.display = 'none';
  }

  const tbody = document.getElementById('history-tbody');
  const empty = document.getElementById('history-empty');
  const items = hist.expired || [];
  if (!items.length) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = items.map(it => `
    <tr>
      <td class="num">${(it.score || 0).toFixed(0)}</td>
      <td><a class="title" href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a></td>
      <td class="hide-sm">${esc(it.buyer)}</td>
      <td>${esc(it.country_name || it.country)}</td>
      <td class="date">${esc((it.deadline || '').slice(0, 10))}</td>
      <td class="date">${it.contract_end ? esc(it.contract_end.slice(0, 10)) : '—'}</td>
      <td class="src hide-sm"><span class="src-chip">${esc(it.source)}</span></td>
    </tr>`).join('');
}

main().catch(err => {
  document.getElementById('empty').style.display = 'block';
  document.getElementById('empty').textContent = "Impossible de charger data.json (" + err + ").";
});
