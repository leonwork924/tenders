// No build step, no framework, no login: fetch data.json (written by the
// fetch pipeline and committed by GitHub Actions) and render it. Re-deploys
// on every push, so this file just has to render whatever is in data.json
// today.

const state = { q: '', src: '', country: '', urgentOnly: false, branchOnly: false, sortKey: 'score', sortAsc: false, activities: new Set(), regions: new Set() };

const ACTIVITY_LABELS = {
  records_management: 'Records & archive management',
  digitisation: 'Digitisation',
  av_media: 'Audiovisual & media',
  heritage: 'Heritage',
  mobility: 'Relocation & mobility',
  fine_art: "Fine art",
  hospitality: 'Hospitality',
  support: 'Support',
};

// Extracts activity categories from the "matched" field (e.g. "records_management(12.0): ..."
// becomes "records_management") -- same categories as tools/keywords_manual.yaml.
function activitiesOf(item) {
  return (item.matched || '')
    .split('|')
    .map(s => s.trim().split('(')[0].trim())
    .filter(s => s && ACTIVITY_LABELS[s]);
}

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

  let regionOf = {};
  try {
    const regionsRes = await fetch('regions.json', {cache: 'no-store'});
    regionOf = await regionsRes.json();
  } catch (e) {
    console.warn('regions.json unavailable, region filter disabled', e);
  }

  // Countries where Mobilitas has a branch (any network) -- built from
  // Mobilitas_Branches_ALL_.csv (115 countries), matched against both
  // country_name and the raw country code, either of which data.json may
  // carry depending on the source (formats aren't consistent source to
  // source -- some give ISO alpha-2, some alpha-3, a few give neither).
  let branchSet = new Set();
  try {
    const branchesRes = await fetch('branches.json', {cache: 'no-store'});
    const branchesData = await branchesRes.json();
    branchSet = new Set(branchesData.variants || []);
  } catch (e) {
    console.warn('branches.json unavailable, branch filter disabled', e);
  }
  function hasBranch(it) {
    return branchSet.has(String(it.country_name || '').toLowerCase())
        || branchSet.has(String(it.country || '').toLowerCase());
  }

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

  // Activity buttons -- only categories actually present in today's data.
  const activitiesPresent = new Set();
  (data.items || []).forEach(it => activitiesOf(it).forEach(a => activitiesPresent.add(a)));
  const activityEl = document.getElementById('activity-filter');
  Array.from(activitiesPresent).sort().forEach(a => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'filter-btn';
    btn.textContent = ACTIVITY_LABELS[a] || a;
    btn.dataset.activity = a;
    btn.addEventListener('click', () => {
      if (state.activities.has(a)) state.activities.delete(a); else state.activities.add(a);
      btn.classList.toggle('on');
      refresh();
    });
    activityEl.appendChild(btn);
  });

  // Region buttons -- derived from the countries present in today's data.
  const regionsPresent = new Set();
  (data.items || []).forEach(it => {
    const r = regionOf[it.country_name] || regionOf[it.country];
    if (r) regionsPresent.add(r);
  });
  const regionEl = document.getElementById('region-filter');
  Array.from(regionsPresent).sort().forEach(r => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'filter-btn';
    btn.textContent = r;
    btn.addEventListener('click', () => {
      if (state.regions.has(r)) state.regions.delete(r); else state.regions.add(r);
      btn.classList.toggle('on');
      refresh();
    });
    regionEl.appendChild(btn);
  });

  document.getElementById('q').addEventListener('input', e => { state.q = e.target.value.toLowerCase(); refresh(); });
  srcSel.addEventListener('change', e => { state.src = e.target.value; refresh(); });
  countrySel.addEventListener('change', e => { state.country = e.target.value; refresh(); });
  document.getElementById('urgent-only').addEventListener('change', e => { state.urgentOnly = e.target.checked; refresh(); });
  document.getElementById('branch-only').addEventListener('change', e => { state.branchOnly = e.target.checked; refresh(); });
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
      <td class="${s.ok ? 'ok' : 'soon'}">${s.ok ? 'OK' : (s.last_run ? 'failed' : 'never run')}</td>
      <td class="num">${s.active_count}</td>
    </tr>`).join('') || '<tr><td colspan="4">No data yet.</td></tr>';
  document.getElementById('health-toggle').addEventListener('click', () => {
    healthPanel.style.display = healthPanel.style.display === 'none' ? 'block' : 'none';
  });

  function refresh() {
    let items = data.items || [];
    if (state.country) items = items.filter(it => it.country === state.country);
    if (state.src) items = items.filter(it => it.source === state.src);
    if (state.urgentOnly) items = items.filter(it => it.days_left !== '' && it.days_left !== null && parseInt(it.days_left, 10) <= 7);
    if (state.branchOnly) items = items.filter(hasBranch);
    if (state.activities.size) items = items.filter(it => activitiesOf(it).some(a => state.activities.has(a)));
    if (state.regions.size) items = items.filter(it => state.regions.has(regionOf[it.country_name] || regionOf[it.country]));
    if (state.q) {
      items = items.filter(it => [it.title, it.buyer, it.country_name, it.matched, it.source, it.description]
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
          ${it.is_new ? '<span class="new-badge">New</span>' : ''}
          <span class="why">${esc((it.matched || '').slice(0, 180))}</span>
          ${it.description ? `<details class="summary"><summary>summary</summary>${esc(it.description)}</details>` : ''}
        </td>
        <td class="hide-sm">${esc(it.buyer)}</td>
        <td>${esc(it.country_name || it.country)}${hasBranch(it) ? ' <span class="branch-badge" title="Country with a Mobilitas branch">🏢</span>' : ''}</td>
        <td class="date">
          ${esc((it.deadline || '').slice(0, 10))}
          ${it.days_left !== '' && it.days_left !== null ? `<span class="chip ${urgency(it.days_left)}">${esc(it.days_left)}d</span>` : ''}
        </td>
        <td class="num hide-sm">${esc(money(it.value, it.currency))}</td>
        <td class="src hide-sm"><span class="src-chip">${esc(it.source)}</span></td>
      </tr>`).join('');
  }

  refresh();
  loadHistory();
}

// --- History / renewals -------------------------------------
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
    document.getElementById('history-empty').textContent = "Could not load history.json.";
    return;
  }

  const alertBox = document.getElementById('renewal-alert');
  if ((hist.renewals || []).length) {
    alertBox.style.display = 'block';
    alertBox.innerHTML = `<b>⏰ ${hist.renewals.length} contract(s) coming up for renewal in the next 6 months</b>
      — the market is likely to come back up for bid, worth watching to re-tender:
      <ul>${hist.renewals.map(r => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>
        — ${esc(r.country_name || r.country)} — estimated contract end ${esc((r.contract_end || '').slice(0,10))}</li>`).join('')}</ul>`;
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
  document.getElementById('empty').textContent = "Could not load data.json (" + err + ").";
});
