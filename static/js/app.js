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


  //finalize button
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
  try {
    // Reset the conversation (DB + session id)
    const res1 = await fetch('/reset_conv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
      credentials: 'same-origin'
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
  document.getElementById('agentChatForm').onsubmit = (e) => {
    e.preventDefault();

    const messageInput = document.getElementById('agentMessage');
    const transcriptDiv = document.getElementById('agentChatTranscript');
    const typingIndicator = document.getElementById('typingIndicator');
    const message = messageInput.value.trim();
    const language = document.getElementById('languageMode').value;

    if (!message) {
      alert('Please enter a message!');
      return;
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
      const transcriptDiv = document.getElementById('agentChatTranscript');
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


//login
// --- CSRF helper ---
let CSRF_TOKEN = null;
async function loadCsrf() {
  const r = await fetch('/csrf-token', { credentials: 'same-origin' });
  const j = await r.json();
  CSRF_TOKEN = j.csrfToken;
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
async function signup(email, password) {
  const r = await fetch('/auth/signup', {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'same-origin',
    body: JSON.stringify({ email, password })
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

// --- UI toggles ---
function showAuth() {
  document.getElementById('auth-gate').style.display = '';
  document.getElementById('app-wrapper').style.display = 'none';
}
function showApp(user) {
  document.getElementById('auth-gate').style.display = 'none';
  document.getElementById('app-wrapper').style.display = '';
  document.getElementById('whoami').textContent =
    `${user.email} — roles: ${user.roles.join(', ')}`;
}

// --- Wire up forms on load ---
window.addEventListener('DOMContentLoaded', async () => {
  await loadCsrf();
  const me = await getMe();

  if (me.authenticated) {
    showApp(me.user);
  } else {
    showAuth();
  }

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
    const email = document.getElementById('signup-email').value.trim().toLowerCase();
    const password = document.getElementById('signup-password').value;
    try {
      const res = await signup(email, password);
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
  chatModeEl.addEventListener('change', () => applyModeUI(chatModeEl.value));
});
// --- LIVE (Mic) via WebSocket; finals only on UI ---
let liveMediaStream = null, liveRecorder = null, liveWS = null, liveActive = false;
let lastFinalText = "", lastFinalAt = 0;

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

    if (msg.type === 'final') {
      const text = (msg.text || '').trim();
      if (!text) return;
      const now = Date.now();
      if (text === lastFinalText && (now - lastFinalAt) < 2000) return; // dedupe rapid repeats
      lastFinalText = text; lastFinalAt = now;

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

      // Recommender ONLY on finals
      const es = new EventSource(`/agent_chat_stream?message=${encodeURIComponent(text)}&lang=${encodeURIComponent(uiLang)}&role=patient&mode=live`);
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

  const startBtn = document.getElementById('startLiveBtn');
  const stopBtn  = document.getElementById('stopLiveBtn');
  if (startBtn) startBtn.disabled = false;
  if (stopBtn)  stopBtn.disabled  = true;
  const s = document.getElementById('liveStatus'); if (s) s.textContent = '';

  liveMediaStream = null; liveRecorder = null; liveWS = null;

  // If in "unasked" mode, fetch & display only the unasked questions now
  if (subMode === 'unasked') {
    try {
      const r = await fetch('/live/unasked', { credentials: 'same-origin' });
      const res = await r.json();
      const ctn = document.getElementById('liveSuggestedQuestions');
      if (ctn) {
        ctn.innerHTML = ''; // clear any previous
        const list = (res?.unasked || res?.questions || []).map(q => {
          if (typeof q === 'string') return q;
          if (q?.text) return q.text;
          if (q?.question?.english || q?.question?.swahili) {
            return `${q.question.english || ''}${q.question.english && q.question.swahili ? ' / ' : ''}${q.question.swahili || ''}`;
          }
          return '';
        }).filter(Boolean);

        if (!list.length) {
          ctn.innerHTML = '<li class="text-muted">All covered ✅</li>';
        } else {
          list.forEach(txt => {
            const li = document.createElement('li');
            li.textContent = txt;
            ctn.appendChild(li);
          });
        }
      }
      // update badge if helper exists
      try { typeof refreshUnaskedBadge === 'function' && refreshUnaskedBadge(); } catch {}
    } catch {}
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
  // use your existing helper if available; otherwise fallback to a simple inline modal open
  try { typeof openUnaskedModal === 'function' ? openUnaskedModal() : null; } catch {}
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
