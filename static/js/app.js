// Updated app.js (adds "Unasked (end-only)" end-of-conversation pop-up list sorted by score)
// Source base: your uploaded app.js :contentReference[oaicite:0]{index=0}

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById('searchForm');
  const queryInput = document.getElementById('searchQuery');
  const maxResultsInput = document.getElementById('maxResults');
  const resultsSection = document.getElementById('resultsSection');
  const resultsContainer = document.getElementById('resultsContainer');
  const suggestedQuestions = document.getElementById('suggestedQuestions');

  //----------------------------------------------------//
  // Default role
  let currentRole = "clinician";

  // Role selector buttons
  const roleDisplay = document.getElementById("currentRoleDisplay");

  document.getElementById("roleClinicianBtn").addEventListener("click", () => {
    currentRole = "clinician";
    roleDisplay.textContent = `(Current: Clinician)`;
  });

  document.getElementById("rolePatientBtn").addEventListener("click", () => {
    currentRole = "patient";
    roleDisplay.textContent = `(Current: Patient)`;
  });

  // finalize button
  document.getElementById('finalizeBtn').addEventListener('click', () => {
    const language = document.getElementById('languageMode').value;
    const transcriptDiv = document.getElementById('agentChatTranscript');

    // open SSE with role=Finalize (message is just a label)
    const mode = document.getElementById('chatMode').value; // <- read the current mode
    const eventSource = new EventSource(
      `/agent_chat_stream?message=${encodeURIComponent('[Finalize]')}&lang=${language}&role=finalize&mode=${mode}`
    );

    eventSource.onmessage = (event) => {
      const item = JSON.parse(event.data);

      // recommender won't appear on finalize, but guard anyway
      if (item.type === 'question_recommender') return;

      const p = document.createElement('p');
      p.innerHTML = `<strong>${item.role}:</strong><br>${(item.message || '').replaceAll('\n','<br>')}<br>
                    <small class="text-muted">${item.timestamp}</small>`;
      transcriptDiv.appendChild(p);
      transcriptDiv.scrollTop = transcriptDiv.scrollHeight;
    };

    eventSource.onerror = (e) => {
      console.error('SSE error (finalize):', e);
      eventSource.close();
    };
  });

  document.getElementById('resetBtn').addEventListener('click', async () => {
    const patientSelect = document.getElementById('patientSelect');
    const patientId = patientSelect && patientSelect.value ? patientSelect.value : null;
    await resetConversation(patientId);
  });

  //----------------------------------------------------//
  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const query = queryInput.value.trim();
    const maxResults = parseInt(maxResultsInput.value, 10);

    if (!query) {
      alert('Please enter a query!');
      return;
    }

    resultsContainer.innerHTML = '';
    suggestedQuestions.innerHTML = '';
    resultsSection.style.display = 'none';

    try {
      const response = await fetch('/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query, max_results: maxResults })
      });

      if (!response.ok) throw new Error('Server error');
      const data = await response.json();

      if (data.error) {
        alert('Error: ' + data.error);
        return;
      }

      if (data.total_results === 0) {
        resultsContainer.innerHTML = '<p class="text-muted">No similar cases found.</p>';
      } else {
        data.results.forEach(result => {
          const card = document.createElement('div');
          card.className = 'card mb-3';
          card.innerHTML = `
            <div class="card-body">
              <h5 class="card-title">Case ID: ${result.case_id}</h5>
              <p><strong>Similarity:</strong> ${result.similarity_score}</p>
              <p><strong>Patient Background:</strong> ${result.patient_background.english || ''}</p>
              <p><strong>Chief Complaint:</strong> ${result.chief_complaint.english || ''}</p>
              <p><strong>Medical History:</strong> ${result.medical_history.english || ''}</p>
              <p><strong>Opening Statement:</strong> ${result.opening_statement.english || ''}</p>
              <p><strong>Red Flags:</strong> ${Object.values(result.red_flags).join(', ') || 'None'}</p>
              <p><strong>Suspected Illness:</strong> ${result.Suspected_illness || 'None'}</p>
              <p><strong>Top Questions:</strong></p>
              <ul>
                ${result.recommended_questions.map(q => `
                  <li>
                    <strong>English:</strong> ${q.question.english || ''}<br>
                    <strong>Swahili:</strong> ${q.question.swahili || ''}
                  </li>`).join('')}
              </ul>
            </div>
          `;
          resultsContainer.appendChild(card);
        });
      }

      if (data.suggested_questions && data.suggested_questions.length > 0) {
        data.suggested_questions.forEach(q => {
          const li = document.createElement('li');
          li.innerHTML = `<strong>English:</strong> ${q.question.english}<br><strong>Swahili:</strong> ${q.question.swahili}`;
          suggestedQuestions.appendChild(li);
        });
      } else {
        suggestedQuestions.innerHTML = '<li class="text-muted">No suggested questions.</li>';
      }

      resultsSection.style.display = 'block';
    } catch (error) {
      console.error('Fetch error:', error);
      alert('An error occurred. Please try again.');
    }
  });

  // Agent chat logic
  document.getElementById('agentChatForm').onsubmit = async (e) => {
    e.preventDefault();

    const messageInput = document.getElementById('agentMessage');
    const transcriptDiv = document.getElementById('agentChatTranscript');
    const typingIndicator = document.getElementById('typingIndicator');
    const message = messageInput.value.trim();
    const language = document.getElementById('languageMode').value;
    const patientSelect = document.getElementById('patientSelect');

    if (!message) {
      alert('Please enter a message!');
      return;
    }

    // Ensure session knows the selected patient for this conversation
    if (patientSelect && patientSelect.value) {
      await syncSessionPatient(patientSelect.value);
    }

    typingIndicator.style.display = 'block';

    const mode = document.getElementById('chatMode').value;
    const eventSource = new EventSource(
      `/agent_chat_stream?message=${encodeURIComponent(message)}&lang=${language}&role=${encodeURIComponent(currentRole)}&mode=${mode}`
    );

    eventSource.onmessage = (event) => {
      const item = JSON.parse(event.data);

      // Debug
      console.log("SSE Received:", item);

      if (item.type === "question_recommender") {
        const qContainer = document.getElementById('chatSuggestedQuestions');

        const li = document.createElement('li');
        li.innerHTML = `<strong>English:</strong> ${item.question.english || ''}<br>
                        <strong>Swahili:</strong> ${item.question.swahili || ''}`;
        qContainer.appendChild(li);
        return;
      }

      // Normal speaker message
      const p = document.createElement('p');
      p.innerHTML = `<strong>${item.role}:</strong><br>${(item.message || '').replaceAll('\n', '<br>')}<br>
                    <small class="text-muted">${item.timestamp}</small>`;
      transcriptDiv.appendChild(p);
      transcriptDiv.scrollTop = transcriptDiv.scrollHeight;
    };

    eventSource.onerror = (error) => {
      console.error('SSE error:', error);
      eventSource.close();
      typingIndicator.style.display = 'none';
    };

    eventSource.onopen = () => {
      typingIndicator.style.display = 'block';
    };

    eventSource.addEventListener("message", () => {
      setTimeout(() => {
        typingIndicator.style.display = 'none';
      }, 500);
    });

    messageInput.value = '';
  };

  // Voice recording and transcription
  let mediaRecorder;
  let audioChunks = [];

  const recordBtn = document.getElementById("recordAudioBtn");
  const audioElement = document.getElementById("recordedAudio");

  if (recordBtn) {
    recordBtn.addEventListener("click", async () => {
      if (!mediaRecorder || mediaRecorder.state === "inactive") {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({
            audio: {
              channelCount: 1,
              sampleRate: 48000,
              noiseSuppression: true,
              echoCancellation: true,
              autoGainControl: true
            }
          });
          const options = { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 128000 };
          mediaRecorder = new MediaRecorder(stream, options);
          audioChunks = [];

          mediaRecorder.ondataavailable = event => audioChunks.push(event.data);
          mediaRecorder.onstop = async () => {
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            audioElement.src = URL.createObjectURL(audioBlob);
            audioElement.style.display = "block";

            const formData = new FormData();
            formData.append("audio", audioBlob);

            const lang = document.getElementById('languageMode').value;
            formData.append("lang", lang);
            formData.append("role", currentRole);

            recordBtn.textContent = "⌛ Transcribing...";

            try {
              const response = await fetch("/transcribe_audio", {
                method: "POST",
                body: formData,
              });

              const data = await response.json();
              if (data.text) {
                const input = document.getElementById("agentMessage");
                input.value = data.text;

                // Optional: Display transcription
                const transcriptDiv = document.getElementById('agentChatTranscript');
                const preview = document.createElement('p');
                preview.innerHTML = `<strong>Transcribed Audio:</strong> ${data.text}`;
                transcriptDiv.appendChild(preview);

                // Submit form programmatically
                const form = document.getElementById("agentChatForm");
                form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
              } else {
                alert("Failed to transcribe audio.");
              }
            } catch (err) {
              alert("Transcription error.");
              console.error(err);
            }

            recordBtn.textContent = "🎤 Record Voice Note";
          };

          mediaRecorder.start();
          recordBtn.textContent = "⏹ Stop Recording";
        } catch (error) {
          alert("Microphone access denied.");
          console.error(error);
        }
      } else if (mediaRecorder.state === "recording") {
        mediaRecorder.stop();
      }
    });
  }
});

// login
// --- CSRF helper ---
let CSRF_TOKEN = null;
async function loadCsrf() {
  const r = await fetch('/csrf-token', { credentials: 'same-origin' });
  const j = await r.json();
  CSRF_TOKEN = j.csrfToken;

  // IMPORTANT: many parts of the file reference window.CSRF_TOKEN already
  window.CSRF_TOKEN = CSRF_TOKEN;
}
function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-CSRFToken': CSRF_TOKEN || ''
  };
}

// --- Small API helpers ---
async function getMe() {
  const r = await fetch('/auth/me', { credentials: 'same-origin' });
  return r.json();
}
async function login(email, password, remember=true) {
  const r = await fetch('/auth/login', {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify({ email, password, remember })
  });
  return r.json();
}
async function signup(email, password, username) {
  const r = await fetch('/auth/signup', {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify({ email, password, username: username || '' })
  });
  return r.json();
}
async function logout() {
  const r = await fetch('/auth/logout', {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin'
  });
  return r.json();
}

// --- UI toggles (auth-gate / app-wrapper only exist when not logged in / logged in respectively) ---
function showAuth() {
  const gate = document.getElementById('auth-gate');
  const wrapper = document.getElementById('app-wrapper');
  if (gate) gate.style.display = '';
  if (wrapper) wrapper.style.display = 'none';
}
function showApp(user) {
  const gate = document.getElementById('auth-gate');
  const wrapper = document.getElementById('app-wrapper');
  if (gate) gate.style.display = 'none';
  if (wrapper) wrapper.style.display = '';
  const whoami = document.getElementById('whoami');
  if (whoami) {
    const name = user.username || (user.email && user.email.split('@')[0]) || 'User';
    whoami.textContent = `${name} — ${(user.roles || []).join(', ')}`;
  }
  loadPatients();
}

// --- Patients (optional for new conversation) ---
async function syncSessionPatient(patientId) {
  try {
    const body = patientId
      ? { patient_id: parseInt(patientId, 10) }
      : {};
    await fetch('/api/session-patient', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
      credentials: 'same-origin',
      body: JSON.stringify(body)
    });
  } catch (e) {
    console.warn('Could not sync session patient:', e);
  }
}

async function resetConversation(patientId) {
  try {
    const res1 = await fetch('/reset_conv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
      credentials: 'same-origin',
      body: JSON.stringify(patientId ? { patient_id: parseInt(patientId, 10) } : {})
    });
    const data1 = await res1.json();

    // Always also reset the live question plan
    await fetch('/live/reset_plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
      credentials: 'same-origin'
    });

    if (data1.ok) {
      // Clear transcript UI
      document.getElementById('agentChatTranscript').innerHTML = '';
      document.getElementById('chatSuggestedQuestions').innerHTML = '';

      // Clear live UI too
      const liveTranscript = document.getElementById('liveTranscript');
      if (liveTranscript) liveTranscript.innerHTML = '';
      const liveSuggested = document.getElementById('liveSuggestedQuestions');
      if (liveSuggested) liveSuggested.innerHTML = '';
      const badge = document.getElementById('unasked-badge');
      if (badge) badge.textContent = '0';

      console.log('Conversation + live plan reset.', data1.conversation_id);
    } else {
      console.error('Reset failed:', data1);
    }
  } catch (err) {
    console.error('Reset error:', err);
  }
}

async function loadPatients() {
  const sel = document.getElementById('patientSelect');
  if (!sel) return;
  const currentVal = sel.value;
  sel.innerHTML = '';
  try {
    const r = await fetch('/api/patients', { credentials: 'same-origin' });
    const data = await r.json();
    if (data.ok && data.patients && data.patients.length > 0) {
      data.patients.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.id;
        opt.textContent = p.label || ('Patient ' + p.id);
        sel.appendChild(opt);
      });
      if (currentVal) sel.value = currentVal;
      else sel.value = data.patients[0].id;
      await syncSessionPatient(sel.value || null);
    } else {
      const opt = document.createElement('option');
      opt.disabled = true;
      opt.textContent = 'No patients — add one';
      sel.appendChild(opt);
    }
  } catch (e) {
    console.warn('Could not load patients:', e);
  }
}

function wireNewPatientButton() {
  const btn = document.getElementById('newPatientBtn');
  const sel = document.getElementById('patientSelect');
  if (!btn || !sel) return;
  btn.addEventListener('click', async () => {
    try {
      const r = await fetch('/api/patients', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
        credentials: 'same-origin',
        body: JSON.stringify({})
      });
      const data = await r.json();
      if (data.ok && data.patient_id) {
        await loadPatients();
        sel.value = String(data.patient_id);
        await syncSessionPatient(sel.value);
      } else {
        alert(data.error || 'Failed to create patient');
      }
    } catch (e) {
      alert('Network error: ' + e.message);
    }
  });
}

// --- Wire up forms on load ---
window.addEventListener('DOMContentLoaded', async () => {
  await loadCsrf();
  let me;
  try {
    me = await getMe();
  } catch (e) {
    me = null;
  }

  wireNewPatientButton();
  const patientSelect = document.getElementById('patientSelect');
  if (patientSelect) {
    patientSelect.addEventListener('change', async () => {
      const patientId = patientSelect.value || null;
      await syncSessionPatient(patientId);
      // Start a fresh conversation when patient changes
      await resetConversation(patientId);
    });
  }

  // When logged in, auth-gate is not in DOM; when not, app-wrapper is not in DOM. Only toggle if both exist (SPA nav).
  if (me && me.authenticated) {
    showApp(me.user);
  } else if (me && me.authenticated === false) {
    showAuth();
  }
  // If getMe failed: server already chose which branch to render; do nothing.

  // Login form
  const loginForm = document.getElementById('login-form');
  loginForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('login-email').value.trim().toLowerCase();
    const password = document.getElementById('login-password').value;
    try {
      const res = await login(email, password);
      if (res.ok) {
        showApp(res.user);
        location.reload();
      } else {
        document.getElementById('auth-error').textContent = res.error || 'Login failed';
        document.getElementById('auth-error').style.display = '';
        await loadCsrf(); // refresh token after an auth error
      }
    } catch {
      document.getElementById('auth-error').textContent = 'Network error';
      document.getElementById('auth-error').style.display = '';
    }
  });

  // Signup form
  const signupForm = document.getElementById('signup-form');
  signupForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('signup-username')?.value?.trim() || '';
    const email = document.getElementById('signup-email').value.trim().toLowerCase();
    const password = document.getElementById('signup-password').value;
    try {
      const res = await signup(email, password, username);
      if (res.ok) {
        // immediately log them in
        const res2 = await login(email, password);
        if (res2.ok) {
          showApp(res2.user);
          location.reload();
        }
      } else {
        document.getElementById('auth-error').textContent = res.error || 'Signup failed';
        document.getElementById('auth-error').style.display = '';
        await loadCsrf();
      }
    } catch {
      document.getElementById('auth-error').textContent = 'Network error';
      document.getElementById('auth-error').style.display = '';
    }
  });

  // Logout
  document.getElementById('logout-btn')?.addEventListener('click', async () => {
    try {
      await logout();
      location.reload();
    } finally {
      showAuth();
      await loadCsrf();
    }
  });
});

// --- Auth card toggles ---
function showLoginCard() {
  document.getElementById('login-card')?.classList.remove('d-none');
  document.getElementById('signup-card')?.classList.add('d-none');
  const err = document.getElementById('auth-error');
  if (err) { err.classList.add('d-none'); err.textContent = ''; }
}
function showSignupCard() {
  document.getElementById('signup-card')?.classList.remove('d-none');
  document.getElementById('login-card')?.classList.add('d-none');
  const err = document.getElementById('auth-error');
  if (err) { err.classList.add('d-none'); err.textContent = ''; }
}

// Make the links work
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action="show-signup"], [data-action="show-login"]');
  if (!el) return;
  e.preventDefault();
  if (el.dataset.action === 'show-signup') showSignupCard();
  if (el.dataset.action === 'show-login') showLoginCard();
});

// Ensure showAuth() defaults to login card
const _origShowAuth = showAuth;
window.showAuth = function() {
  _origShowAuth();
  showLoginCard();
};



// --- UI Mode toggling (presentation only) ---
document.addEventListener('DOMContentLoaded', () => {
  const convWrap   = document.getElementById('agentsConversation');
  const chatModeEl = document.getElementById('chatMode');
  const modeBadge  = document.getElementById('modeBadge');
  const modeTipTxt = document.getElementById('modeTipText');
  const msgInput   = document.getElementById('agentMessage');

  if (!chatModeEl || !convWrap) return; // page not in the app state yet

  // Ensure the "Live (Mic)" option exists in the selector
  if (![...chatModeEl.options].some(o => o.value === 'live')) {
    chatModeEl.add(new Option('Live (Mic)', 'live'));
  }

  function ensureLiveUI() {
    let bar = document.getElementById('liveBar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'liveBar';
      bar.className = 'd-flex gap-2 align-items-center mb-2';
      (document.getElementById('agentsConversation') || document.body).prepend(bar);
    }
    if (!document.getElementById('startLiveBtn')) {
      const s = document.createElement('button');
      s.id = 'startLiveBtn';
      s.type = 'button';
      s.className = 'btn btn-sm btn-primary';
      s.textContent = '▶ Start Live';
      bar.appendChild(s);
    }
    if (!document.getElementById('stopLiveBtn')) {
      const t = document.createElement('button');
      t.id = 'stopLiveBtn';
      t.type = 'button';
      t.className = 'btn btn-sm btn-danger';
      t.textContent = '■ Stop';
      t.disabled = true;
      bar.appendChild(t);
    }
    if (!document.getElementById('liveStatus')) {
      const sp = document.createElement('span');
      sp.id = 'liveStatus';
      sp.className = 'ms-2 text-muted';
      sp.textContent = '';
      bar.appendChild(sp);
    }
    return bar;
  }

  function toggleLiveControls(show) {
    const bar = ensureLiveUI();
    bar.style.display = show ? '' : 'none';
  }

  function applyModeUI(val) {
    const turn = document.getElementById('turnPane') || convWrap; // fallback to whole conv area
    const live = document.getElementById('livePane');             // optional separate live pane

    // defaults
    if (turn) turn.style.display = '';
    if (live) live.style.display = 'none';

    convWrap.classList.remove('mode-real', 'mode-simulated', 'mode-live');

    if (val === 'simulated') {
      convWrap.classList.add('mode-simulated');
      if (modeBadge)  modeBadge.textContent = 'Simulated';
      if (modeTipTxt) modeTipTxt.textContent =
        'Simulated chat: the system can generate patient responses. Please reset conversation before continuing';
      if (msgInput)   msgInput.placeholder = 'Say something to the doctor...';
      toggleLiveControls(false);

    } else if (val === 'live') {
      convWrap.classList.add('mode-live');
      if (modeBadge)  modeBadge.textContent = 'Live (Mic)';
      if (modeTipTxt) modeTipTxt.textContent =
        'Speak and get continuous recommendations. Press Stop to end; use Finalize for summary/plan.';

      // show toolbar always; swap panes if a dedicated live pane exists
      toggleLiveControls(true);
      if (live) {
        if (turn) turn.style.display = 'none';
        live.style.display = '';
      }

    } else { // real actors
      convWrap.classList.add('mode-real');
      if (modeBadge)  modeBadge.textContent = 'Real Actors';
      if (modeTipTxt) modeTipTxt.textContent =
        'Turn-based chat: alternate between Clinician and Patient. Please reset conversation before continuing';
      if (msgInput)   msgInput.placeholder = 'Say something to the doctor...';
      toggleLiveControls(false);
    }
  }

  // init + listen
  applyModeUI(chatModeEl.value);
  chatModeEl.addEventListener('change', async () => {
    applyModeUI(chatModeEl.value);
    const patientSelect = document.getElementById('patientSelect');
    const patientId = patientSelect && patientSelect.value ? patientSelect.value : null;
    await resetConversation(patientId);
  });
});


// ------------------------------ LIVE (Mic) ------------------------------ //
// --- LIVE (Mic) via WebSocket; finals only on UI ---
let liveMediaStream = null, liveRecorder = null, liveWS = null, liveActive = false;
let lastFinalText = "", lastFinalAt = 0;

// Throttle recommendations in Live/Normal mode so they don't flood the clinician.
const LIVE_RECO_MIN_INTERVAL_MS = 7000;   // one suggestion at most every 7s
const LIVE_UI_MAX_SUGGESTIONS = 3;        // keep only last N suggestions in the list
let liveRecoLastAt = 0;
let liveRecoES = null;

function wsURL(path) {
  const base = new URL(window.location.origin);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${base.origin}${path.startsWith('/') ? path : '/' + path}`;
}

// sub-mode (defaults to "normal")
function getLiveRecoMode() {
  return document.getElementById('liveRecoMode')?.value === 'unasked' ? 'unasked' : 'normal';
}

// reveal header controls if present (no-op if not in DOM)
function revealLiveControls() {
  const a = document.getElementById('show-unasked-btn');
  const b = document.getElementById('clear-suggested-btn');
  if (a) a.style.display = 'inline-flex';
  if (b) b.style.display = 'inline-flex';
}

// escape to avoid HTML injection in unasked rendering
function escapeHtml(str) {
  return String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

// clear Suggested list locally + tell backend to reset plan
async function clearSuggestedNow() {
  const ctn = document.getElementById('liveSuggestedQuestions');
  if (ctn) ctn.innerHTML = '';
  try {
    await fetch('/live/reset_plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
      credentials: 'same-origin'
    });
  } catch {}
  // reset badge if present
  try { document.getElementById('unasked-badge').textContent = '0'; } catch {}
  // optional: refresh badge via your helper if it exists
  try { typeof refreshUnaskedBadge === 'function' && refreshUnaskedBadge(); } catch {}
}

// NEW: fetch Listener Summary + Final Plan (always), and optionally render Unasked questions below it.
async function fetchStopBundleAndRender({ renderUnasked = false, showModal = false } = {}) {
  const uiLang = document.getElementById('languageMode')?.value || 'bilingual';

  const res = await fetch('/live/stop_bundle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
    credentials: 'same-origin',
    body: JSON.stringify({ lang: uiLang })
  });

  if (!res.ok) {
    console.warn('Failed to fetch stop bundle:', res.status);
    return;
  }

  const data = await res.json();

  // --- Render Listener block ---
  const card = document.getElementById('livePostStopCard');
  const out  = document.getElementById('liveListenerOutput');
  const tsEl = document.getElementById('livePostStopTs');

  const listenerMsg = (data?.listener?.message || '').toString();
  const listenerTs  = (data?.listener?.timestamp || '').toString();

  if (out) {
    // Basic, safe rendering: escape HTML then convert newlines
    out.innerHTML = escapeHtml(listenerMsg).replaceAll('\n', '<br>');
  }
  if (tsEl) tsEl.textContent = listenerTs;
  if (card) card.style.display = '';

  // reveal follow-up assistant below (optional)
  try { setupFollowupChatOnce(); } catch {}

  if (!renderUnasked) return;

  // --- Render Unasked below the Listener section ---
  let items = Array.isArray(data?.unasked) ? data.unasked : [];
  items = items
    .map(it => {
      if (typeof it === 'string') return { question: it, score: null };
      if (it && typeof it === 'object') {
        const q = (it.question ?? it.text ?? '').toString().trim();
        const s = (it.score !== undefined && it.score !== null) ? Number(it.score) : null;
        return { question: q, score: Number.isFinite(s) ? s : null };
      }
      return null;
    })
    .filter(Boolean)
    .filter(it => it.question);

  // sort desc by score (null goes last)
  items.sort((a, b) => {
    const as = (a.score === null ? -Infinity : a.score);
    const bs = (b.score === null ? -Infinity : b.score);
    return bs - as;
  });

  const ul = document.getElementById('liveSuggestedQuestions');
  if (ul) {
    ul.innerHTML = '';
    if (items.length === 0) {
      ul.innerHTML = '<li class="text-muted">All covered ✅</li>';
    } else {
      items.forEach((it, idx) => {
        const li = document.createElement('li');
        const scoreTxt = (it.score === null) ? '' : ` <span class="text-muted">(${it.score.toFixed(3)})</span>`;
        li.innerHTML = `<strong>${idx + 1}.</strong> ${escapeHtml(it.question)}${scoreTxt}`;
        ul.appendChild(li);
      });
    }
  }

  // badge
  const badge = document.getElementById('unasked-badge');
  if (badge) badge.textContent = String(items.length);

  // modal (optional)
  if (showModal) {
    const modalList = document.getElementById('unasked-list');
    if (modalList) {
      modalList.innerHTML = '';
      if (items.length === 0) {
        modalList.innerHTML = '<div class="text-muted">All covered ✅</div>';
      } else {
        const listEl = document.createElement('ul');
        listEl.className = 'mb-0';
        items.forEach((it, idx) => {
          const li = document.createElement('li');
          const scoreTxt = (it.score === null) ? '' : ` <span class="text-muted">(${it.score.toFixed(3)})</span>`;
          li.innerHTML = `<strong>${idx + 1}.</strong> ${escapeHtml(it.question)}${scoreTxt}`;
          listEl.appendChild(li);
        });
        modalList.appendChild(listEl);
      }
    }
    if (window.bootstrap?.Modal) {
      const modalEl = document.getElementById('unaskedModal');
      if (modalEl) new bootstrap.Modal(modalEl).show();
    }
  }
}


// ------------------------------ Live Follow-up Chatbot ------------------------------
let followupReady = false;

function appendFollowupTurn(role, text) {
  const box = document.getElementById('liveFollowupTranscript');
  if (!box) return;
  const div = document.createElement('div');
  div.className = 'mb-2';
  const who = (role === 'assistant') ? 'Assistant' : 'Clinician';
  div.innerHTML = `<div><strong>${who}:</strong></div><div>${escapeHtml(text).replaceAll('\n','<br>')}</div>`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function setFollowupBusy(isBusy) {
  const btn = document.getElementById('liveFollowupSend');
  const inp = document.getElementById('liveFollowupInput');
  if (btn) btn.disabled = !!isBusy;
  if (inp) inp.disabled = !!isBusy;
}

function setupFollowupChatOnce() {
  if (followupReady) return;
  followupReady = true;

  const card = document.getElementById('liveFollowupCard');
  const body = document.getElementById('liveFollowupBody');
  const toggleBtn = document.getElementById('toggleFollowupBtn');
  const form = document.getElementById('liveFollowupForm');
  const input = document.getElementById('liveFollowupInput');

  if (toggleBtn && body) {
    toggleBtn.addEventListener('click', () => {
      const hidden = body.style.display === 'none';
      body.style.display = hidden ? '' : 'none';
      toggleBtn.textContent = hidden ? 'Hide' : 'Show';
    });
  }

  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = (input?.value || '').trim();
      if (!msg) return;

      appendFollowupTurn('clinician', msg);
      if (input) input.value = '';

      const uiLang = document.getElementById('languageMode')?.value || 'bilingual';

      setFollowupBusy(true);
      try {
        const res = await fetch('/live/followup_chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
          credentials: 'same-origin',
          body: JSON.stringify({ message: msg, lang: uiLang })
        });

        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const err = data?.error || `Request failed (${res.status})`;
          appendFollowupTurn('assistant', err);
          return;
        }

        const answer = (data?.answer || '').toString();
        appendFollowupTurn('assistant', answer || '—');
      } catch (err) {
        appendFollowupTurn('assistant', 'Network error: could not reach the server.');
      } finally {
        setFollowupBusy(false);
      }
    });
  }

  // show card if present
  if (card) card.style.display = '';
}



async function startLive() {
  // guard: only in live mode
  if (document.getElementById('chatMode')?.value !== 'live') {
    alert('Switch Mode to "Live (Mic)" first.'); return;
  }
  if (liveActive) return;
  liveActive = true;

  const mime = 'audio/webm;codecs=opus';
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported(mime)) {
    alert('Your browser does not support WebM/Opus recording.'); liveActive = false; return;
  }

  // mic
  try {
    liveMediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, noiseSuppression: true, echoCancellation: true, autoGainControl: true }
    });
  } catch {
    alert('Microphone access denied.'); liveActive = false; return;
  }

  // --- language mapping: UI -> WS codes ---
  const uiLang = document.getElementById('languageMode')?.value || 'bilingual';
  const lang = (uiLang === 'english') ? 'en' : (uiLang === 'swahili' ? 'sw' : 'bilingual');

  // ws
  liveWS = new WebSocket(wsURL(`/ws/stt?lang=${encodeURIComponent(lang)}`));
  liveWS.binaryType = 'arraybuffer';
  liveWS.onopen  = () => { const s = document.getElementById('liveStatus'); if (s) s.textContent = 'listening…'; };
  liveWS.onerror = () => { const s = document.getElementById('liveStatus'); if (s) s.textContent = 'connection error'; };
  liveWS.onclose = () => { const s = document.getElementById('liveStatus'); if (s) s.textContent = ''; };

  // finals-only UI
  liveWS.onmessage = (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    if (msg.type === 'partial') return; // keep UI clean

    // STT status events (retrying/fallback)
    if (msg.type === 'status') {
      const banner = document.getElementById('sttDegradedBanner');
      const textEl = document.getElementById('sttDegradedText');
      if (banner && textEl) {
        textEl.textContent = msg.message || 'Transcription may be delayed.';
        banner.style.display = '';
      }
      return;
    }

    if (msg.type === 'final') {
      const text = (msg.text || '').trim();
      if (!text) return;
      const now = Date.now();
      if (text === lastFinalText && (now - lastFinalAt) < 2000) return; // dedupe rapid repeats
      lastFinalText = text; lastFinalAt = now;

      // If we were in degraded mode, hide the banner after we get a successful final.
      try {
        const banner = document.getElementById('sttDegradedBanner');
        if (banner && banner.style.display !== 'none') {
          setTimeout(() => { try { banner.style.display = 'none'; } catch {} }, 2500);
        }
      } catch {}

      // let other modules listen
      try { window.dispatchEvent(new CustomEvent('live:final', { detail: { text, lang } })); } catch {}

      // Transcript (NO "Patient:" prefix)
      const transcriptEl = document.getElementById('liveTranscript');
      if (transcriptEl) {
        const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const p = document.createElement('p');
        p.innerHTML = `${text.replaceAll('\n','<br>')}<br><small class="text-muted">${ts}</small>`;
        transcriptEl.appendChild(p);
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
      }

      // Recommender ONLY on finals (throttled in "normal" mode)
      // - In Normal mode, we rate-limit how often we ask the LLM for suggestions.
      // - In Unasked(end-only) mode, we still build the backend plan, but only show the list at Stop.
      const recoMode = getLiveRecoMode();
      const nowReco = Date.now();

      // If we're in normal mode and it's too soon, skip asking for another suggestion.
      if (recoMode === 'normal' && (nowReco - liveRecoLastAt) < LIVE_RECO_MIN_INTERVAL_MS) {
        return;
      }
      liveRecoLastAt = nowReco;

      // Close any in-flight recommender stream to avoid back-to-back suggestion bursts.
      try { liveRecoES?.close?.(); } catch {}
      liveRecoES = new EventSource(`/agent_chat_stream?message=${encodeURIComponent(text)}&lang=${encodeURIComponent(uiLang)}&role=patient&mode=live`);
      const es = liveRecoES;
      es.onmessage = (event) => {
        let item; try { item = JSON.parse(event.data); } catch { return; }
        if ((item.role || '').toLowerCase() === 'patient') return;

        if (item.type === 'question_recommender') {
          // Always send to backend to build plan (asked vs unasked)
          const en = (item.question?.english || '').trim();
          const sw = (item.question?.swahili || '').trim();
          const payload = { required: [] };
          if (en) payload.required.push({ id: `en_${en.slice(0,48)}`, text: en });
          if (sw) payload.required.push({ id: `sw_${sw.slice(0,48)}`, text: sw });

          fetch('/live/plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
            credentials: 'same-origin',
            body: JSON.stringify(payload)
          })
          .then(() => { try { typeof refreshUnaskedBadge === 'function' && refreshUnaskedBadge(); } catch {} })
          .catch(()=>{});

          // UI (sub-mode aware)
          if (getLiveRecoMode() === 'normal') {
            const ctn = document.getElementById('liveSuggestedQuestions');
            if (ctn) {
              const li = document.createElement('li');
              li.innerHTML = `<strong>English:</strong> ${item.question?.english || ''}<br>
                              <strong>Swahili:</strong> ${item.question?.swahili || ''}`;
              ctn.appendChild(li);
              // Keep the list short so the clinician can actually read it
              while (ctn.children.length > LIVE_UI_MAX_SUGGESTIONS) {
                ctn.removeChild(ctn.firstElementChild);
              }
            }
          }

          // make sure header controls are visible
          revealLiveControls();
          return;
        }

        // (optional) other agent messages
        if (item.message) {
          const host = document.getElementById('liveTranscript');
          if (host) {
            const p = document.createElement('p');
            p.innerHTML = `<strong>${item.role || 'Agent'}:</strong><br>${item.message.replaceAll('\n','<br>')}
                           ${item.timestamp ? `<br><small class="text-muted">${item.timestamp}</small>` : ''}`;
            host.appendChild(p);
            host.scrollTop = host.scrollHeight;
          }
        }
      };
      es.onerror = () => es.close();
      setTimeout(() => es.close(), 8000);
    }
  };

  // recorder → ws
  liveRecorder = new MediaRecorder(liveMediaStream, { mimeType: mime, audioBitsPerSecond: 32000 });
  liveRecorder.addEventListener('dataavailable', (evt) => {
    if (!liveActive || !evt.data || !evt.data.size) return;
    if (liveWS?.readyState === WebSocket.OPEN) {
      evt.data.arrayBuffer().then(buf => { try { liveWS.send(buf); } catch {} });
    }
  });
  liveRecorder.start(250);

  // buttons
  const startBtn = document.getElementById('startLiveBtn');
  const stopBtn  = document.getElementById('stopLiveBtn');
  if (startBtn) startBtn.disabled = true;
  if (stopBtn)  stopBtn.disabled  = false;

  // show header controls right away in Live
  revealLiveControls();
}

async function stopLive() {
  const subMode = getLiveRecoMode();

  liveActive = false;
  try { liveRecorder?.requestData?.(); } catch {}
  await new Promise(r => setTimeout(r, 60));
  try {
    if (liveRecorder && liveRecorder.state !== 'inactive') {
      await new Promise(res => { liveRecorder.addEventListener('stop', res, { once:true }); liveRecorder.stop(); });
    }
  } catch {}
  try { liveMediaStream?.getTracks().forEach(t => t.stop()); } catch {}
  try { liveWS?.close(); } catch {}
  try { liveRecoES?.close?.(); } catch {}
  liveRecoES = null;

  const startBtn = document.getElementById('startLiveBtn');
  const stopBtn  = document.getElementById('stopLiveBtn');
  if (startBtn) startBtn.disabled = false;
  if (stopBtn)  stopBtn.disabled  = true;
  const s = document.getElementById('liveStatus'); if (s) s.textContent = '';

  liveMediaStream = null; liveRecorder = null; liveWS = null;

  // ✅ NEW: always show Listener Summary + Final Plan after STOP,
  // and in "unasked" mode also show unasked questions *below* that section.
  try {
    await fetchStopBundleAndRender({
      renderUnasked: (subMode === 'unasked'),
      showModal: (subMode === 'unasked')
    });
  } catch (err) {
    console.warn('Stop bundle render failed:', err);
  }
}

// Wire live buttons only; leave your Real/Sim handlers intact
document.addEventListener('click', (e) => {
  if (e.target?.id === 'startLiveBtn') startLive();
  if (e.target?.id === 'stopLiveBtn')  stopLive();
});

// If user switches mode away from Live, auto-stop; also reveal controls in Live
document.getElementById('chatMode')?.addEventListener('change', (e) => {
  const turn = document.getElementById('turnPane');
  const live = document.getElementById('livePane');
  if (turn && live) {
    if (e.target.value === 'live') { turn.style.display = 'none'; live.style.display = ''; revealLiveControls(); }
    else { turn.style.display = ''; live.style.display = 'none'; if (liveActive) stopLive(); }
  }
});

// Button handlers (if present in DOM)
document.getElementById('show-unasked-btn')?.addEventListener('click', () => {
  // use your existing helper if available; otherwise fallback to fetch+modal render
  try {
    if (typeof openUnaskedModal === 'function') openUnaskedModal();
    else fetchStopBundleAndRender({ renderUnasked: true, showModal: true });
  } catch {}
});
document.getElementById('clear-suggested-btn')?.addEventListener('click', clearSuggestedNow);

// app-wide event for other modules to react to finals
window.addEventListener('live:final', (ev) => {
  const { text /*, lang*/ } = ev.detail;
  // Mark asked on each final (safe if CSRF exempt; otherwise include token)
  fetch('/live/mark_asked', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
    credentials: 'same-origin',
    body: JSON.stringify({ text })
  }).then(() => {
    try { typeof refreshUnaskedBadge === 'function' && refreshUnaskedBadge(); } catch {}
  }).catch(()=>{});
});
