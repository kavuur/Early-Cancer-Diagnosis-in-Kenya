// static/js/admin.js - Redesigned Admin Dashboard

// ===== Utility Functions =====
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

function showAlert(message, type = 'error') {
  const alert = document.getElementById('admin-alert');
  const alertText = document.getElementById('admin-alert-text');
  if (alert && alertText) {
    alertText.textContent = message;
    alert.classList.add('show');
    setTimeout(() => alert.classList.remove('show'), 5000);
  }
}

function showLoading() {
  document.getElementById('loading-overlay')?.classList.add('show');
}

function hideLoading() {
  document.getElementById('loading-overlay')?.classList.remove('show');
}

// ===== Tab Navigation =====
function initTabs() {
  const tabs = document.querySelectorAll('.admin-tab');
  const panels = document.querySelectorAll('.tab-content-panel');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const tabName = tab.getAttribute('data-tab');

      // Update active tab
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      // Update active panel
      panels.forEach(p => p.classList.remove('active'));
      document.getElementById(`tab-${tabName}`)?.classList.add('active');

      // Load data for specific tabs on first view
      if (tabName === 'clinicians' && !window.__cliniciansLoaded) {
        window.__cliniciansLoaded = true;
        loadCliniciansData();
      }
      if (tabName === 'users' && !window.__usersLoaded) {
        window.__usersLoaded = true;
        loadUsersData();
      }
    });
  });
}

// ===== Modal Functions =====
function openModal(modalId) {
  document.getElementById(modalId)?.classList.add('show');
}

function closeModal(modalId) {
  document.getElementById(modalId)?.classList.remove('show');
}

window.closeModal = closeModal; // Make available globally for onclick handlers

// ===== KPI Rendering =====
function renderKPIs(summary) {
  const { users, conversations, messages } = summary;

  // Total Users
  document.getElementById('kpi-total-users').textContent = users.total || 0;
  document.getElementById('kpi-users-breakdown').textContent =
    `${users.clinicians || 0} clinicians, ${users.admins || 0} admins`;

  // Conversations
  document.getElementById('kpi-total-convos').textContent = conversations.total || 0;

  // Messages
  document.getElementById('kpi-total-messages').textContent = messages.total || 0;
  document.getElementById('kpi-messages-breakdown').textContent =
    `${messages.patient || 0} patient, ${messages.clinician || 0} clinician`;

  // Recommended Questions
  document.getElementById('kpi-reco-questions').textContent = messages.recommended || 0;
}

// ===== Charts =====
let _convChart, _symChart;

function renderConversationsChart(summary) {
  const ctx = document.getElementById('chart-convos');
  if (!ctx) return;

  const data = summary.series?.conversations_per_day || [];
  const labels = data.map(([date]) => {
    const d = new Date(date);
    return `${d.getMonth() + 1}/${d.getDate()}`;
  });
  const values = data.map(([, count]) => count);

  if (_convChart) _convChart.destroy();

  _convChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Conversations',
        data: values,
        borderColor: '#7bc148',
        backgroundColor: 'rgba(123, 193, 72, 0.1)',
        tension: 0.4,
        fill: true
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 }
        }
      }
    }
  });
}

function renderTopCliniciansTable(summary) {
  const tbody = document.querySelector('#tbl-top-clinicians tbody');
  if (!tbody) return;

  tbody.innerHTML = '';
  const clinicians = summary.series?.top_clinicians || [];

  if (clinicians.length === 0) {
    tbody.innerHTML = '<tr><td colspan="2" class="text-muted">No data</td></tr>';
    return;
  }

  clinicians.forEach(c => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(c.display_name || c.email || '—')}</td>
      <td style="text-align: right; font-weight: 600;">${c.count}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderGlobalSymptomsChart(symptomsData) {
  const ctx = document.getElementById('chart-symptoms');
  if (!ctx) return;

  const entries = Object.entries(symptomsData.global || {});
  if (entries.length === 0) {
    ctx.parentElement.innerHTML = '<p class="text-muted">No symptoms data available</p>';
    return;
  }

  const top20 = entries.slice(0, 20);
  const labels = top20.map(([symptom]) => symptom);
  const counts = top20.map(([, count]) => count);

  if (_symChart) _symChart.destroy();

  _symChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Mentions',
        data: counts,
        backgroundColor: '#7bc148',
        borderRadius: 4
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: {
        legend: { display: false }
      },
      scales: {
        x: {
          beginAtZero: true,
          ticks: { precision: 0 }
        }
      }
    }
  });
}

// ===== Conversations Management =====
const conversationState = {
  page: 1,
  size: 20,
  loading: false,
  done: false,
  clinicianId: null,
  allConversations: [] // Store for search filtering
};

function getConversationsUrl() {
  const params = new URLSearchParams({
    page: conversationState.page,
    size: conversationState.size
  });
  if (conversationState.clinicianId) {
    params.set('clinician_id', conversationState.clinicianId);
  }
  return `/admin/api/conversations?${params}`;
}

async function loadConversations(append = false) {
  if (conversationState.loading) return;
  if (append && conversationState.done) return;

  if (!append) {
    conversationState.page = 1;
    conversationState.done = false;
  }

  conversationState.loading = true;
  showLoading();

  try {
    const data = await getJSON(getConversationsUrl());
    if (!data.ok) throw new Error(data.error || 'Failed to load conversations');

    if (!append) {
      conversationState.allConversations = data.conversations || [];
      renderConversationsTable(conversationState.allConversations);
    } else {
      conversationState.allConversations.push(...(data.conversations || []));
      renderConversationsTable(conversationState.allConversations, true);
    }

    conversationState.page += 1;

    const loaded = (conversationState.page - 1) * conversationState.size;
    if (loaded >= (data.total || 0)) {
      conversationState.done = true;
      const btn = document.getElementById('load-more-convos');
      if (btn) btn.disabled = true;
    } else {
      const btn = document.getElementById('load-more-convos');
      if (btn) btn.disabled = false;
    }
  } catch (err) {
    showAlert(err.message);
  } finally {
    conversationState.loading = false;
    hideLoading();
  }
}

function renderConversationsTable(conversations, append = false) {
  const tbody = document.querySelector('#tbl-convos tbody');
  if (!tbody) return;

  if (!append) tbody.innerHTML = '';

  if (conversations.length === 0 && !append) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No conversations found</td></tr>';
    return;
  }

  conversations.forEach(conv => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-family: monospace; font-size: 0.85rem;">${escapeHtml(String(conv.id).substring(0, 8))}...</td>
      <td>${escapeHtml(conv.owner_display_name || '—')}</td>
      <td>${escapeHtml(conv.patient_label || '—')}</td>
      <td style="font-size: 0.85rem;">${fmtDateTime(conv.created_at)}</td>
      <td style="text-align: center;">${conv.message_count || 0}</td>
      <td style="text-align: right;">
        <button class="btn-admin-secondary btn-admin-sm" onclick="viewConversation('${escapeHtml(conv.id)}')">View</button>
        <button class="btn-admin-danger btn-admin-sm" onclick="deleteConversation('${escapeHtml(conv.id)}')">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function viewConversation(convId) {
  showLoading();
  try {
    const data = await getJSON(`/admin/api/conversation/${encodeURIComponent(convId)}`);
    if (!data.ok) throw new Error(data.error || 'Failed to load conversation');

    const messages = data.messages || [];
    const recs = data.recommended_questions || [];

    const messagesHtml = messages.map(m => `
      <div style="margin-bottom: 1rem; padding: 0.75rem; background: #f9fafb; border-radius: 8px;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
          <strong style="color: #7bc148;">${escapeHtml(m.role || 'Unknown')}</strong>
          <span style="font-size: 0.8rem; color: #6b7280;">${escapeHtml(m.timestamp || '')}</span>
        </div>
        <div>${escapeHtml(m.text || m.message || '')}</div>
      </div>
    `).join('');

    const recsHtml = recs.length > 0 ? recs.map(r => `
      <li style="margin-bottom: 0.5rem;">
        ${escapeHtml(r.question)}
        ${r.symptom ? `<span class="status-badge active">${escapeHtml(r.symptom)}</span>` : ''}
      </li>
    `).join('') : '<p class="text-muted">No recommended questions</p>';

    const detailBody = document.getElementById('conversation-detail-body');
    if (detailBody) {
      detailBody.innerHTML = `
        <div style="margin-bottom: 1.5rem;">
          <h6 style="margin-bottom: 1rem;">Conversation ID: ${escapeHtml(convId)}</h6>
        </div>
        <div style="margin-bottom: 1.5rem;">
          <h6 style="margin-bottom: 0.75rem;">Transcript</h6>
          <div style="max-height: 400px; overflow-y: auto;">
            ${messagesHtml}
          </div>
        </div>
        <div>
          <h6 style="margin-bottom: 0.75rem;">Recommended Questions</h6>
          <ul style="list-style: none; padding: 0;">
            ${recsHtml}
          </ul>
        </div>
      `;
    }

    openModal('modal-conversation');
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

async function deleteConversation(convId) {
  if (!confirm(`Are you sure you want to delete conversation ${convId}?\n\nThis action cannot be undone.`)) {
    return;
  }

  showLoading();
  try {
    const response = await fetch(`/admin/api/conversation/${encodeURIComponent(convId)}`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': window.CSRF_TOKEN || ''
      }
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Delete failed: ${text}`);
    }

    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'Delete failed');

    // Reload conversations list
    await loadConversations(false);
    showAlert('Conversation deleted successfully', 'success');
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

window.viewConversation = viewConversation;
window.deleteConversation = deleteConversation;

// ===== Clinicians Management =====
async function loadCliniciansData() {
  showLoading();
  try {
    const data = await getJSON('/admin/api/clinicians');
    if (!data.ok) throw new Error(data.error || 'Failed to load clinicians');

    renderCliniciansTable(data.clinicians || []);
    populateClinicianDropdowns(data.clinicians || []);
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

function renderCliniciansTable(clinicians) {
  const tbody = document.querySelector('#tbl-clinicians tbody');
  if (!tbody) return;

  tbody.innerHTML = '';

  if (clinicians.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No clinicians found</td></tr>';
    return;
  }

  clinicians.forEach(c => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${c.id}</td>
      <td>${escapeHtml(c.display_name || '—')}</td>
      <td>${escapeHtml(c.email || '—')}</td>
      <td style="text-align: center; font-weight: 600;">${c.conversations || 0}</td>
      <td style="text-align: right;">
        <button class="btn-admin-secondary btn-admin-sm" onclick="filterByClinician(${c.id})">View Conversations</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function populateClinicianDropdowns(clinicians) {
  // Filter dropdown in conversations tab
  const filterSelect = document.getElementById('filter-clinician');
  if (filterSelect) {
    filterSelect.innerHTML = '<option value="">All Clinicians</option>';
    clinicians.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = `${c.display_name || c.email || `User ${c.id}`} (${c.conversations} convos)`;
      filterSelect.appendChild(opt);
    });
  }

  // Create patient modal dropdown
  const patientSelect = document.getElementById('patient-clinician-select');
  if (patientSelect) {
    patientSelect.innerHTML = '<option value="">Select a clinician</option>';
    clinicians.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = c.display_name || c.email || `User ${c.id}`;
      patientSelect.appendChild(opt);
    });
  }
}

function filterByClinician(clinicianId) {
  // Switch to conversations tab and apply filter
  document.querySelector('[data-tab="conversations"]')?.click();

  const filterSelect = document.getElementById('filter-clinician');
  if (filterSelect) {
    filterSelect.value = clinicianId;
    filterSelect.dispatchEvent(new Event('change'));
  }
}

window.filterByClinician = filterByClinician;

// ===== User Management =====
let availableRoles = [];

async function loadUsersData() {
  showLoading();
  try {
    // Load roles first
    const rolesData = await getJSON('/admin/api/roles');
    if (rolesData.ok) {
      availableRoles = rolesData.roles || [];
    }

    // Load users
    const data = await getJSON('/admin/api/users');
    if (!data.ok) throw new Error(data.error || 'Failed to load users');

    renderUsersTable(data.users || []);
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

function renderUsersTable(users) {
  const tbody = document.querySelector('#tbl-users tbody');
  if (!tbody) return;

  tbody.innerHTML = '';

  if (users.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No users found</td></tr>';
    return;
  }

  users.forEach(u => {
    const rolesHtml = u.roles.length > 0
      ? u.roles.map(r => `<span class="status-badge active">${escapeHtml(r)}</span>`).join(' ')
      : '<span class="text-muted">No roles</span>';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${u.id}</td>
      <td>${escapeHtml(u.email)}</td>
      <td>${escapeHtml(u.username || '—')}</td>
      <td>${rolesHtml}</td>
      <td style="font-size: 0.85rem;">${u.created_at ? fmtDateTime(u.created_at) : '—'}</td>
      <td style="text-align: right;">
        <button class="btn-admin-secondary btn-admin-sm" onclick="openEditUserModal(${u.id})">Edit</button>
        <button class="btn-admin-danger btn-admin-sm" onclick="deleteUser(${u.id})">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function populateRoleCheckboxes(containerId, selectedRoles = []) {
  const container = document.getElementById(containerId);
  if (!container) return;

  container.innerHTML = '';
  availableRoles.forEach(role => {
    const isChecked = selectedRoles.includes(role.name);
    const div = document.createElement('div');
    div.innerHTML = `
      <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
        <input type="checkbox" name="roles" value="${escapeHtml(role.name)}" ${isChecked ? 'checked' : ''}>
        <span>${escapeHtml(role.name)}</span>
      </label>
    `;
    container.appendChild(div);
  });
}

function openCreateUserModal() {
  populateRoleCheckboxes('user-roles-checkboxes');
  openModal('modal-create-user');
}

async function handleCreateUser(event) {
  event.preventDefault();

  const form = event.target;
  const formData = new FormData(form);
  const roles = Array.from(form.querySelectorAll('input[name="roles"]:checked'))
    .map(cb => cb.value);

  const data = {
    email: formData.get('email'),
    username: formData.get('username'),
    password: formData.get('password'),
    roles: roles
  };

  showLoading();
  try {
    const response = await fetch('/admin/api/users', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': window.CSRF_TOKEN || ''
      },
      body: JSON.stringify(data)
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Failed to create user: ${text}`);
    }

    const result = await response.json();
    if (!result.ok) throw new Error(result.error || 'Failed to create user');

    closeModal('modal-create-user');
    form.reset();
    showAlert(`User created successfully: ${result.email}`, 'success');

    // Reload users list
    await loadUsersData();
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

async function openEditUserModal(userId) {
  showLoading();
  try {
    const data = await getJSON('/admin/api/users');
    if (!data.ok) throw new Error(data.error || 'Failed to load user');

    const user = data.users.find(u => u.id === userId);
    if (!user) throw new Error('User not found');

    document.getElementById('edit-user-id').value = user.id;
    document.getElementById('edit-user-email').value = user.email;
    document.getElementById('edit-user-username').value = user.username || '';

    populateRoleCheckboxes('edit-user-roles-checkboxes', user.roles);
    openModal('modal-edit-user');
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

async function handleEditUser(event) {
  event.preventDefault();

  const form = event.target;
  const formData = new FormData(form);
  const userId = formData.get('user_id');
  const roles = Array.from(form.querySelectorAll('input[name="roles"]:checked'))
    .map(cb => cb.value);

  const data = {
    username: formData.get('username'),
    roles: roles
  };

  showLoading();
  try {
    const response = await fetch(`/admin/api/users/${userId}`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': window.CSRF_TOKEN || ''
      },
      body: JSON.stringify(data)
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Failed to update user: ${text}`);
    }

    const result = await response.json();
    if (!result.ok) throw new Error(result.error || 'Failed to update user');

    closeModal('modal-edit-user');
    showAlert('User updated successfully', 'success');

    // Reload users list
    await loadUsersData();
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

async function deleteUser(userId) {
  if (!confirm('Are you sure you want to delete this user?\n\nThis action cannot be undone.')) {
    return;
  }

  showLoading();
  try {
    const response = await fetch(`/admin/api/users/${userId}`, {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': window.CSRF_TOKEN || ''
      }
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Delete failed: ${text}`);
    }

    const data = await response.json();
    if (!data.ok) throw new Error(data.error || 'Delete failed');

    showAlert('User deleted successfully', 'success');

    // Reload users list
    await loadUsersData();
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

window.openCreateUserModal = openCreateUserModal;
window.handleCreateUser = handleCreateUser;
window.openEditUserModal = openEditUserModal;
window.handleEditUser = handleEditUser;
window.deleteUser = deleteUser;

// ===== Patient Management =====
function openCreatePatientModal() {
  openModal('modal-create-patient');
}

async function handleCreatePatient(event) {
  event.preventDefault();

  const form = event.target;
  const formData = new FormData(form);
  const data = {
    identifier: formData.get('identifier'),
    display_name: formData.get('display_name'),
    clinician_id: parseInt(formData.get('clinician_id'))
  };

  showLoading();
  try {
    const response = await fetch('/admin/api/patients', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': window.CSRF_TOKEN || ''
      },
      body: JSON.stringify(data)
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Failed to create patient: ${text}`);
    }

    const result = await response.json();
    if (!result.ok) throw new Error(result.error || 'Failed to create patient');

    closeModal('modal-create-patient');
    form.reset();
    showAlert(`Patient created successfully: ${result.identifier}`, 'success');
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

window.openCreatePatientModal = openCreatePatientModal;
window.handleCreatePatient = handleCreatePatient;

// ===== Analytics (Symptoms & Disease Likelihoods) =====
async function loadAnalyticsData() {
  showLoading();
  try {
    const data = await getJSON('/admin/api/symptoms');
    if (!data.ok) throw new Error(data.error || 'Failed to load symptoms data');

    renderGlobalSymptomsChart(data);
    renderPerConversationSymptoms(data.by_conversation || []);
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

function renderPerConversationSymptoms(conversations) {
  const tbody = document.querySelector('#tbl-conv-symptoms tbody');
  if (!tbody) return;

  tbody.innerHTML = '';

  if (conversations.length === 0) {
    tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">No symptom data available</td></tr>';
    return;
  }

  conversations.forEach(conv => {
    const symptoms = Object.entries(conv.symptoms || {})
      .slice(0, 5)
      .map(([s, count]) => `${escapeHtml(s)} (${count})`)
      .join(', ') || '—';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-family: monospace; font-size: 0.85rem;">${escapeHtml(String(conv.conversation_id).substring(0, 12))}</td>
      <td>${symptoms}</td>
      <td style="text-align: right;">
        <button class="btn-admin-secondary btn-admin-sm" onclick="viewDiseaseLikelihoods('${escapeHtml(conv.conversation_id)}')">Analyze</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

async function viewDiseaseLikelihoods(convId) {
  showLoading();
  try {
    const data = await getJSON(`/admin/api/conversation/${encodeURIComponent(convId)}/disease_likelihoods`);
    if (!data.ok) throw new Error(data.error || 'Failed to load disease likelihoods');

    const panel = document.getElementById('disease-likelihood-panel');
    if (!panel) return;

    const symptomsText = Object.entries(data.symptoms || {})
      .map(([s, count]) => `${escapeHtml(s)} (${count})`)
      .join(', ') || 'None detected';

    const diseasesHtml = (data.top_diseases || []).length > 0
      ? data.top_diseases.map(d => `
          <tr>
            <td><strong>${escapeHtml(d.disease)}</strong></td>
            <td style="text-align: right;">
              <span style="color: #7bc148; font-weight: 600;">${d.likelihood_pct}%</span>
            </td>
          </tr>
        `).join('')
      : '<tr><td colspan="2" class="text-muted">No disease predictions available</td></tr>';

    panel.innerHTML = `
      <div style="margin-bottom: 1rem;">
        <h6 style="margin-bottom: 0.5rem;">Conversation: ${escapeHtml(String(convId).substring(0, 12))}...</h6>
        <p style="font-size: 0.9rem; color: #6b7280; margin: 0;">
          <strong>Symptoms:</strong> ${symptomsText}
        </p>
      </div>
      <div>
        <h6 style="margin-bottom: 0.75rem;">Predicted Diseases</h6>
        <table class="admin-table">
          <thead>
            <tr>
              <th>Disease</th>
              <th style="text-align: right;">Likelihood</th>
            </tr>
          </thead>
          <tbody>${diseasesHtml}</tbody>
        </table>
      </div>
      <div style="margin-top: 1rem; padding: 0.75rem; background: #fef3c7; border-radius: 6px; font-size: 0.85rem;">
        <strong>Note:</strong> These predictions are based on FAISS similarity matching and should not be used for medical diagnosis.
      </div>
    `;
  } catch (err) {
    showAlert(err.message);
  } finally {
    hideLoading();
  }
}

window.viewDiseaseLikelihoods = viewDiseaseLikelihoods;

// ===== Search & Filters =====
function initSearchAndFilters() {
  // Clinician filter
  const clinicianFilter = document.getElementById('filter-clinician');
  if (clinicianFilter) {
    clinicianFilter.addEventListener('change', () => {
      conversationState.clinicianId = clinicianFilter.value || null;
      loadConversations(false);
    });
  }

  // Search conversation by ID, clinician name, or patient name
  const searchInput = document.getElementById('search-conversation');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      const query = e.target.value.toLowerCase();
      const filtered = conversationState.allConversations.filter(conv => {
        const id = String(conv.id).toLowerCase();
        const clinician = (conv.owner_display_name || '').toLowerCase();
        const patient = (conv.patient_label || '').toLowerCase();

        return id.includes(query) ||
               clinician.includes(query) ||
               patient.includes(query);
      });
      renderConversationsTable(filtered, false);
    });
  }

  // Load more button
  const loadMoreBtn = document.getElementById('load-more-convos');
  if (loadMoreBtn) {
    loadMoreBtn.addEventListener('click', () => loadConversations(true));
  }
}

// ===== Main Initialization =====
async function adminInit() {
  try {
    // Initialize tabs
    initTabs();

    // Get CSRF token
    try {
      const csrfData = await getJSON('/csrf-token');
      if (csrfData.csrfToken) window.CSRF_TOKEN = csrfData.csrfToken;
    } catch (e) {
      console.warn('Failed to load CSRF token:', e);
    }

    // Load overview data
    showLoading();
    const summary = await getJSON('/admin/api/summary');
    if (!summary.ok) throw new Error(summary.error || 'Failed to load summary');

    renderKPIs(summary);
    renderConversationsChart(summary);
    renderTopCliniciansTable(summary);

    // Load clinicians for dropdowns
    const cliniciansData = await getJSON('/admin/api/clinicians');
    if (cliniciansData.ok) {
      populateClinicianDropdowns(cliniciansData.clinicians || []);
    }

    // Load conversations (first page)
    await loadConversations(false);

    // Load analytics data
    await loadAnalyticsData();

    // Initialize search and filters
    initSearchAndFilters();

    hideLoading();
  } catch (err) {
    hideLoading();
    showAlert(err.message);
    console.error('Admin initialization error:', err);
  }
}

// Run on admin page
if (location.pathname === '/admin' || location.pathname === '/admin/') {
  window.addEventListener('DOMContentLoaded', () => {
    if (window.__ADMIN_INIT_ATTACHED__) return;
    window.__ADMIN_INIT_ATTACHED__ = true;
    adminInit();
  });
}
