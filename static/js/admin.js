// static/js/admin.js

// ---- Helpers ----
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
  ));
}

async function getJSON(url) {
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`HTTP ${r.status}: ${text}`);
  }
  return r.json();
}

function fmtDateTime(iso) {
  try { return new Date(iso).toLocaleString(); } catch { return iso || ''; }
}

// ---- Renderers: existing KPIs/Charts/Tables ----
function renderKPIs(sum) {
  document.getElementById('kpi-users').textContent =
    `Total: ${sum.users.total} | Clinicians: ${sum.users.clinicians} | Admins: ${sum.users.admins}`;
  document.getElementById('kpi-convos').textContent = sum.conversations.total;
  document.getElementById('kpi-messages').textContent =
    `Total: ${sum.messages.total} (P:${sum.messages.patient} C:${sum.messages.clinician})`;
  document.getElementById('kpi-reco').textContent = sum.messages.recommended;
}

let _convChart;
function renderConversationsPerDayChart(sum) {
  const labels = (sum.series?.conversations_per_day || []).map(([d]) => d);
  const data = (sum.series?.conversations_per_day || []).map(([, c]) => c);
  const ctx = document.getElementById('chart-convos');
  if (!ctx) return;
  if (_convChart) _convChart.destroy();
  _convChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets: [{ label: 'Conversations', data }] },
    options: { responsive: true, scales: { y: { beginAtZero: true } } }
  });
}

function renderTopCliniciansTable(sum) {
  const tbodyClin = document.querySelector('#tbl-clinicians tbody');
  if (!tbodyClin) return;
  tbodyClin.innerHTML = '';
  (sum.series?.top_clinicians || []).forEach(row => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${escapeHtml(row.display_name || row.email || '—')}</td><td>${row.count}</td>`;
    tbodyClin.appendChild(tr);
  });
}

// ---- Conversations list (paginated) ----
function renderConversationRows(conversations) {
  const tbody = document.querySelector('#tbl-convos tbody');
  if (!tbody) return;
  conversations.forEach(c => {
    const ownerTxt = c.owner_display_name ?? c.owner_email ?? (c.owner_user_id != null ? String(c.owner_user_id) : '—');
    const patientTxt = c.patient_label ?? (c.patient_id ? 'Patient' : '—');

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="text-truncate" style="max-width:260px">${escapeHtml(c.id)}</td>
      <td>${escapeHtml(String(ownerTxt))}</td>
      <td>${escapeHtml(String(patientTxt))}</td>
      <td>${new Date(c.created_at).toLocaleString()}</td>
      <td class="text-center">${c.message_count ?? 0}</td>
      <td>
        <button class="btn btn-sm btn-outline-primary me-1" data-cid="${escapeHtml(c.id)}">View</button>
        <button class="btn btn-sm btn-outline-danger" data-del="${escapeHtml(c.id)}">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}


async function showConversation(cid) {
  const detail = document.getElementById('conv-detail');
  const err = document.getElementById('admin-error');
  if (!detail) {
    console.error('#conv-detail not found in DOM');
    if (err) { err.style.display = ''; err.textContent = 'Template missing #conv-detail element.'; }
    return;
  }

  const j = await getJSON(`/admin/api/conversation/${encodeURIComponent(cid)}`);
  if (j.ok === false) throw new Error(j.error || 'Failed to load conversation');

  const msgs = (j.messages || []).map(m => ({
    role: m.role,
    timestamp: m.timestamp,
    text: m.text ?? m.raw_text ?? m.message ?? ''
  }));

  const recs = (j.recommended_questions || []).map(q => ({
    question: q.question ?? q.text ?? '',
    symptom: q.symptom || null
  }));

  const msgList = msgs.map(m => `
    <li class="mb-2">
      <strong>${escapeHtml(m.role)}</strong>
      <small class="text-muted"> ${escapeHtml(m.timestamp || '')}</small><br/>
      <span>${escapeHtml(m.text)}</span>
    </li>`).join('');

  const recoList = recs.length
    ? recs.map(q => `
        <li class="mb-2">
          ${escapeHtml(q.question)}
          ${q.symptom ? `<span class="badge bg-info text-dark ms-2">${escapeHtml(q.symptom)}</span>` : ''}
        </li>`).join('')
    : '<em>No recommended questions.</em>';

  detail.innerHTML = `
    <div class="card p-3">
      <h5 class="mb-3">Conversation ${escapeHtml(cid)}</h5>
      <div class="row">
        <div class="col-md-6">
          <h6>Transcript</h6>
          <ul class="list-unstyled mb-0">${msgList}</ul>
        </div>
        <div class="col-md-6">
          <h6>Recommended Questions</h6>
          <ul class="list-unstyled mb-0">${recoList}</ul>
        </div>
      </div>
    </div>`;

  detail.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---- NEW: Symptoms chart + per-conversation table + likelihoods ----
let _symChart;

function renderGlobalSymptoms(symData) {
  const ctx = document.getElementById('chart-symptoms');
  if (!ctx) return;

  const entries = Object.entries(symData.global || {});
  const top = entries.slice(0, 20); // top 20
  const labels = top.map(([k]) => k);
  const counts = top.map(([, v]) => v);

  if (_symChart) _symChart.destroy();
  _symChart = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Symptom mentions (global)', data: counts }] },
    options: {
      responsive: true,
      indexAxis: 'y',
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } }
    }
  });
}

function renderPerConversationSymptoms(symData) {
  const tbody = document.getElementById('tbl-conv-symptoms-body');
  if (!tbody) return;
  tbody.innerHTML = '';
  (symData.by_conversation || []).forEach(row => {
    const ownerTxt = row.owner_display_name ?? row.owner_email ?? (row.owner_user_id != null ? String(row.owner_user_id) : '—');

    const symList = Object.entries(row.symptoms || {}).slice(0, 5)
      .map(([s, c]) => `${escapeHtml(s)} (${c})`).join(', ') || '—';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(String(ownerTxt))}</td>
      <td class="text-truncate" style="max-width:260px"><code>${escapeHtml(row.conversation_id)}</code></td>
      <td>${symList}</td>
      <td><button class="btn btn-sm btn-outline-primary" data-like="${escapeHtml(row.conversation_id)}">View</button></td>
    `;
    tbody.appendChild(tr);
  });
}

function renderLikelihoodPanel(cid, data) {
  const box = document.getElementById('conv-like-box');
  if (!box) return;

  const symptomList = Object.entries(data.symptoms || {})
    .map(([s, c]) => `${escapeHtml(s)} (${c})`).join(', ') || '—';

  const rows = (data.top_diseases || []).map(d => `
    <tr><td>${escapeHtml(d.disease)}</td><td>${d.likelihood_pct}%</td></tr>
  `).join('') || '<tr><td colspan="2" class="text-muted">No signal</td></tr>';

  box.innerHTML = `
    <h6 class="mb-2">Conversation ${escapeHtml(cid)}</h6>
    <p><strong>Extracted symptoms:</strong> ${symptomList}</p>
    <table class="table table-sm mb-0">
      <thead><tr><th>Disease</th><th>Estimated likelihood</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function fetchAndShowLikelihoods(cid) {
  const j = await getJSON(`/admin/api/conversation/${encodeURIComponent(cid)}/disease_likelihoods`);
  if (j.ok === false) throw new Error(j.error || 'Failed to load likelihoods');
  renderLikelihoodPanel(cid, j);
}

// ---- Paging state ----
const convoPager = { page: 1, size: 20, loading: false, done: false, clinicianId: null };

function getConversationsUrl() {
  const params = new URLSearchParams({ page: convoPager.page, size: convoPager.size });
  if (convoPager.clinicianId != null && convoPager.clinicianId !== '') {
    params.set('clinician_id', convoPager.clinicianId);
  }
  return `/admin/api/conversations?${params.toString()}`;
}

async function loadMoreConversations(append) {
  if (convoPager.loading) return;
  if (append && convoPager.done) return;
  if (!append) resetConversationsPager();
  convoPager.loading = true;
  try {
    const j = await getJSON(getConversationsUrl());
    if (j.ok === false) throw new Error(j.error || 'Failed to load conversations');
    if (!append) {
      const tbody = document.querySelector('#tbl-convos tbody');
      if (tbody) tbody.innerHTML = '';
    }
    renderConversationRows(j.conversations || []);
    convoPager.page += 1;

    const loaded = (convoPager.page - 1) * convoPager.size;
    if (loaded >= (j.total || 0)) {
      convoPager.done = true;
      const btn = document.getElementById('load-more');
      if (btn) btn.disabled = true;
    }
  } catch (e) {
    const el = document.getElementById('admin-error');
    if (el) { el.style.display = ''; el.textContent = e.message; }
  } finally {
    convoPager.loading = false;
  }
}

function resetConversationsPager() {
  convoPager.page = 1;
  convoPager.done = false;
  const btn = document.getElementById('load-more');
  if (btn) btn.disabled = false;
}

// ---- Main init ----
async function adminInit() {
  const err = document.getElementById('admin-error');
  try {
    // CSRF token for DELETE
    try {
      const r = await fetch('/csrf-token', { credentials: 'same-origin' });
      const j = await r.json();
      if (j.csrfToken) window.CSRF_TOKEN = j.csrfToken;
    } catch (_) {}

    // Summary/KPIs
    const sum = await getJSON('/admin/api/summary');
    if (sum.ok === false) throw new Error(sum.error || 'Summary failed');

    renderKPIs(sum);
    renderConversationsPerDayChart(sum);
    renderTopCliniciansTable(sum);

    // Clinicians dropdown for filter
    const cliniciansRes = await getJSON('/admin/api/clinicians');
    const clinicians = (cliniciansRes.ok && cliniciansRes.clinicians) ? cliniciansRes.clinicians : [];
    const filterSelect = document.getElementById('admin-clinician-filter');
    if (filterSelect) {
      clinicians.forEach(cl => {
        const opt = document.createElement('option');
        opt.value = cl.id;
        opt.textContent = (cl.display_name || cl.email || `User ${cl.id}`) + ` (${cl.conversations})`;
        filterSelect.appendChild(opt);
      });
      filterSelect.addEventListener('change', () => {
        convoPager.clinicianId = filterSelect.value === '' ? null : filterSelect.value;
        resetConversationsPager();
        const tbody = document.querySelector('#tbl-convos tbody');
        if (tbody) tbody.innerHTML = '';
        loadMoreConversations(false);
      });
    }

    // Conversations list
    await loadMoreConversations(false);

    // Symptoms data -> chart + per-conv table
    const sym = await getJSON('/admin/api/symptoms');
    if (sym.ok === false) throw new Error(sym.error || 'Symptoms failed');
    renderGlobalSymptoms(sym);
    renderPerConversationSymptoms(sym);

    // Bind once
    document.getElementById('load-more')?.addEventListener('click', () => loadMoreConversations(true));

    // Clicks in conversations table (View transcript / Delete)
    document.querySelector('#tbl-convos')?.addEventListener('click', async (e) => {
      const btnView = e.target.closest('button[data-cid]');
      const btnDel  = e.target.closest('button[data-del]');
      try {
        if (btnView) {
          await showConversation(btnView.getAttribute('data-cid'));
        }
        if (btnDel) {
          const cid = btnDel.getAttribute('data-del');
          if (!cid) return;
          if (!confirm(`Delete conversation ${cid}? This cannot be undone.`)) return;
          const r = await fetch(`/admin/api/conversation/${encodeURIComponent(cid)}`, {
            method: 'DELETE',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': (window.CSRF_TOKEN || '') }
          });
          if (!r.ok) {
            const txt = await r.text();
            throw new Error(`Delete failed: ${txt}`);
          }
          const j = await r.json();
          if (j.ok === false) {
            throw new Error(j.error || 'Delete failed');
          }
          // Remove row
          btnDel.closest('tr')?.remove();
          // If this conversation was shown in detail, clear panel
          const detail = document.getElementById('conv-detail');
          if (detail && detail.textContent.includes(cid)) {
            detail.innerHTML = '';
          }
        }
      } catch (ex) {
        if (err) { err.style.display = ''; err.textContent = ex.message; }
      }
    });

    // Clicks in per-conversation symptoms table (Likelihoods)
    document.getElementById('tbl-conv-symptoms-body')?.addEventListener('click', async (e) => {
      const btn = e.target.closest('button[data-like]');
      if (!btn) return;
      try {
        await fetchAndShowLikelihoods(btn.getAttribute('data-like'));
      } catch (ex) {
        if (err) { err.style.display = ''; err.textContent = ex.message; }
      }
    });

  } catch (ex) {
    if (err) { err.style.display = ''; err.textContent = ex.message; }
  }
}

// Run on /admin or /admin/ (tolerate trailing slash)
if (location.pathname === '/admin' || location.pathname === '/admin/') {
  window.addEventListener('DOMContentLoaded', () => {
    if (window.__ADMIN_INIT_ATTACHED__) return; // prevent double-binding
    window.__ADMIN_INIT_ATTACHED__ = true;
    adminInit();
  });
}
