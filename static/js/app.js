// Medical Case Search - Main Application JavaScript

document.addEventListener("DOMContentLoaded", () => {
  // Initialize Lucide icons
  if (typeof lucide !== 'undefined') {
    lucide.createIcons();
  }

  // Global state
  let currentRole = "clinician";
  let messageCount = 0;
  window.lastSuggestion = null; // Store last suggestion to show when switching to Clinician

  // DOM Elements
  const searchBtn = document.getElementById('searchBtn');
  const searchQuery = document.getElementById('searchQuery');
  const maxResults = document.getElementById('maxResults');
  const chatMode = document.getElementById('chatMode');
  const languageMode = document.getElementById('languageMode');
  const roleClinicianBtn = document.getElementById('roleClinicianBtn');
  const rolePatientBtn = document.getElementById('rolePatientBtn');
  const agentMessage = document.getElementById('agentMessage');
  const sendBtn = document.getElementById('sendBtn');
  const recordAudioBtn = document.getElementById('recordAudioBtn');
  const resetBtn = document.getElementById('resetBtn');
  const finalizeBtn = document.getElementById('finalizeBtn');
  const transcriptDiv = document.getElementById('agentChatTranscript');
  const typingIndicator = document.getElementById('typingIndicator');
  const messageCountEl = document.getElementById('messageCount');
  const turnPane = document.getElementById('turnPane');
  const livePane = document.getElementById('livePane');
  const roleSelector = document.getElementById('roleSelector');
  const chatInputWrapper = document.getElementById('chatInputWrapper');
  const inputRoleLabel = document.getElementById('inputRoleLabel');
  const suggestionBox = document.getElementById('suggestionBox');
  const suggestionText = document.getElementById('suggestionText');
  const closeSuggestion = document.getElementById('closeSuggestion');
  const useSuggestion = document.getElementById('useSuggestion');
  const panelRight = document.getElementById('panel-right');
  const resizeRight = document.getElementById('resize-right');
  const closeSummary = document.getElementById('closeSummary');

  // Role Toggle
  if (roleClinicianBtn && rolePatientBtn) {
    roleClinicianBtn.addEventListener('click', () => {
      currentRole = "clinician";
      roleClinicianBtn.className = 'active-clinician';
      rolePatientBtn.className = '';
      updateChatInputStyle();
      // Show stored suggestion when switching to Clinician
      if (window.lastSuggestion?.text && suggestionText && suggestionBox) {
        // Swahili in italics
        suggestionText.innerHTML = window.lastSuggestion.isSwahili
          ? `<em>${window.lastSuggestion.text}</em>`
          : window.lastSuggestion.text;
        suggestionBox.classList.remove('hidden');
      }
    });

    rolePatientBtn.addEventListener('click', () => {
      currentRole = "patient";
      rolePatientBtn.className = 'active-patient';
      roleClinicianBtn.className = '';
      updateChatInputStyle();
      // Hide suggestion box when Patient is selected
      suggestionBox?.classList.add('hidden');
    });
  }

  function updateChatInputStyle() {
    if (!chatInputWrapper || !inputRoleLabel || !sendBtn) return;

    chatInputWrapper.classList.remove('clinician', 'patient', 'simulated');
    inputRoleLabel.classList.remove('clinician', 'patient');
    sendBtn.classList.remove('clinician', 'patient');

    if (chatMode?.value === 'simulated') {
      chatInputWrapper.classList.add('simulated');
      inputRoleLabel.textContent = 'Simulated';
      inputRoleLabel.classList.add('clinician');
      sendBtn.classList.add('clinician');
      agentMessage.placeholder = "Enter scenario or let AI continue...";
    } else {
      chatInputWrapper.classList.add(currentRole);
      inputRoleLabel.textContent = currentRole === 'clinician' ? 'Clinician' : 'Patient';
      inputRoleLabel.classList.add(currentRole);
      sendBtn.classList.add(currentRole);
      agentMessage.placeholder = currentRole === 'clinician'
        ? "Ask the patient a question..."
        : "Respond to the clinician...";
    }
  }

  // Mode Toggle
  if (chatMode) {
    chatMode.addEventListener('change', () => {
      const mode = chatMode.value;

      if (mode === 'live') {
        turnPane?.classList.add('hidden');
        livePane?.classList.remove('hidden');
        if (roleSelector) roleSelector.style.display = 'none';
      } else {
        turnPane?.classList.remove('hidden');
        livePane?.classList.add('hidden');

        if (mode === 'simulated') {
          if (roleSelector) roleSelector.style.display = 'none';
          if (suggestionBox) suggestionBox.style.display = 'none';
        } else {
          if (roleSelector) roleSelector.style.display = '';
          if (suggestionBox) suggestionBox.style.display = '';
        }
      }

      updateChatInputStyle();
    });
  }

  // Suggestion Box
  if (closeSuggestion) {
    closeSuggestion.addEventListener('click', () => {
      suggestionBox?.classList.add('hidden');
      window.lastSuggestion = null; // Clear stored suggestion
    });
  }

  if (useSuggestion && suggestionText && agentMessage) {
    useSuggestion.addEventListener('click', () => {
      agentMessage.value = suggestionText.textContent;
      suggestionBox?.classList.add('hidden'); // Hide after use
      window.lastSuggestion = null; // Clear stored suggestion
      sendMessage(); // Auto-send so new suggestion comes
    });
  }

  // Search
  if (searchBtn && searchQuery) {
    searchBtn.addEventListener('click', async () => {
      const query = searchQuery.value.trim();
      if (!query) {
        alert('Please enter symptoms to search');
        return;
      }

      try {
        const response = await fetch('/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query, max_results: parseInt(maxResults?.value || 5) })
        });

        if (!response.ok) throw new Error('Search failed');
        const data = await response.json();

        // Update suggested questions
        if (data.suggested_questions?.length > 0) {
          const firstQ = data.suggested_questions[0];
          if (suggestionText) {
            suggestionText.textContent = firstQ.question?.english || firstQ.question || '';
          }
          if (suggestionBox) {
            suggestionBox.classList.remove('hidden');
          }
        }
      } catch (error) {
        console.error('Search error:', error);
        alert('Search failed. Please try again.');
      }
    });
  }

  // Track the message we just sent to avoid duplicates
  let lastSentMessage = null;
  let lastSentRole = null;

  // Send Message
  function sendMessage() {
    const message = agentMessage?.value.trim();
    if (!message) {
      alert('Please enter a message');
      return;
    }

    const language = languageMode?.value || 'bilingual';
    const mode = chatMode?.value || 'real';

    // Add the user's message to transcript immediately
    addMessageToTranscript(currentRole, message);

    // Track what we sent to skip the echo from backend
    lastSentMessage = message;
    lastSentRole = currentRole;

    // Hide old suggestion while waiting for new one
    suggestionBox?.classList.add('hidden');

    // Update and show typing indicator
    if (typingIndicator) {
      // The responder is typically the opposite role
      const responderRole = currentRole === 'clinician' ? 'patient' : 'clinician';
      const typingIcon = document.getElementById('typingIcon');
      const typingRole = document.getElementById('typingRole');

      if (typingIcon) {
        typingIcon.className = `typing-icon ${responderRole}`;
        // Use SVG directly instead of relying on Lucide conversion
        if (responderRole === 'clinician') {
          typingIcon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.8 2.3A.3.3 0 1 0 5 2H4a2 2 0 0 0-2 2v5a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6V4a2 2 0 0 0-2-2h-1a.2.2 0 1 0 .3.3"/><path d="M8 15v1a6 6 0 0 0 6 6v0a6 6 0 0 0 6-6v-4"/><circle cx="20" cy="10" r="2"/></svg>';
        } else {
          typingIcon.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
        }
      }
      if (typingRole) {
        typingRole.className = `typing-role ${responderRole}`;
        typingRole.textContent = responderRole === 'clinician' ? 'Clinician' : 'Patient';
      }
      typingIndicator.classList.remove('hidden');
    }

    const eventSource = new EventSource(
      `/agent_chat_stream?message=${encodeURIComponent(message)}&lang=${language}&role=${encodeURIComponent(currentRole)}&mode=${mode}`
    );

    eventSource.onmessage = (event) => {
      const item = JSON.parse(event.data);

      if (item.type === "question_recommender") {
        const englishText = item.question?.english || '';
        const swahiliText = item.question?.swahili || '';
        const questionText = englishText || swahiliText;
        const isSwahili = !englishText && swahiliText;

        // Always store the suggestion (with Swahili flag)
        window.lastSuggestion = { text: questionText, isSwahili };

        const questionTextEl = document.getElementById('suggestionText');
        const suggestionBoxEl = document.getElementById('suggestionBox');

        if (questionText && questionTextEl && suggestionBoxEl) {
          // Swahili in italics
          questionTextEl.innerHTML = isSwahili ? `<em>${questionText}</em>` : questionText;
          // Always show the suggestion - it's for the clinician's next question
          suggestionBoxEl.classList.remove('hidden');
        }
        return;
      }

      // Skip the echoed message (we already added it immediately when sent)
      const itemRole = (item.role || '').toLowerCase();
      const sentRole = (lastSentRole || '').toLowerCase();
      if (lastSentMessage && item.message === lastSentMessage && itemRole === sentRole) {
        lastSentMessage = null;
        lastSentRole = null;
        return;
      }

      // Add message to transcript
      addMessageToTranscript(item.role, item.message, item.timestamp);
    };

    eventSource.onerror = () => {
      console.error('SSE error');
      eventSource.close();
      if (typingIndicator) typingIndicator.classList.add('hidden');
    };

    eventSource.addEventListener("message", () => {
      setTimeout(() => {
        if (typingIndicator) typingIndicator.classList.add('hidden');
      }, 500);
    });

    if (agentMessage) agentMessage.value = '';
  }

  function addMessageToTranscript(role, message, timestamp) {
    if (!transcriptDiv) return;

    const roleClass = role?.toLowerCase().includes('patient') ? 'patient' : 'clinician';
    const roleLabel = roleClass === 'patient' ? 'Patient' : 'Clinician';
    const roleIcon = roleClass === 'patient' ? 'user' : 'stethoscope';
    const time = timestamp || new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    const msgEl = document.createElement('div');
    msgEl.className = `message ${roleClass}`;
    msgEl.innerHTML = `
      <div class="message-header">
        <span class="message-icon ${roleClass}"><i data-lucide="${roleIcon}"></i></span>
        <span class="message-role ${roleClass}">${roleLabel}</span>
        <span class="message-time ${roleClass}">${time}</span>
      </div>
      <p class="message-text">${(message || '').replace(/\n/g, '<br>')}</p>
    `;
    transcriptDiv.appendChild(msgEl);
    lucide.createIcons({ nodes: [msgEl] });
    transcriptDiv.scrollTop = transcriptDiv.scrollHeight;

    messageCount++;
    if (messageCountEl) messageCountEl.textContent = `${messageCount} messages`;
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', sendMessage);
  }

  if (agentMessage) {
    agentMessage.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        sendMessage();
      }
    });
  }

  // Reset Conversation
  if (resetBtn) {
    resetBtn.addEventListener('click', async () => {
      try {
        await fetch('/reset_conv', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
          credentials: 'same-origin'
        });

        await fetch('/live/reset_plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': (window.CSRF_TOKEN || '') },
          credentials: 'same-origin'
        });

        // Clear Transcript
        if (transcriptDiv) transcriptDiv.innerHTML = '';
        const qContainer = document.getElementById('chatSuggestedQuestions');
        if (qContainer) qContainer.innerHTML = '';
        messageCount = 0;
        if (messageCountEl) messageCountEl.textContent = '0 messages';

        // Clear Summary & Plan sections
        const patientSummary = document.getElementById('patientSummary');
        const keyFindings = document.getElementById('keyFindings');
        const differentialDiagnosis = document.getElementById('differentialDiagnosis');
        const recommendedPlan = document.getElementById('recommendedPlan');
        const followUp = document.getElementById('followUp');

        if (patientSummary) patientSummary.innerHTML = 'Click "Summarize & Final Plan" to generate a summary based on the conversation.';
        if (keyFindings) keyFindings.innerHTML = '<li><span class="bullet">&bull;</span> Awaiting conversation data...</li>';
        if (differentialDiagnosis) differentialDiagnosis.innerHTML = '<li><span class="num">1.</span> Pending analysis...</li>';
        if (recommendedPlan) recommendedPlan.innerHTML = '<li><span class="check">&#10003;</span> Awaiting recommendations...</li>';
        if (followUp) followUp.innerHTML = 'Follow-up details will appear here after analysis.';

        // Close Summary & Plan sidebar
        panelRight?.classList.add('hidden');
        resizeRight?.classList.add('hidden');

        // Clear suggestion box
        suggestionBox?.classList.add('hidden');
        window.lastSuggestion = null;

      } catch (err) {
        console.error('Reset error:', err);
      }
    });
  }

  // Finalize Button - Show Summary Panel
  if (finalizeBtn) {
    finalizeBtn.addEventListener('click', () => {
      if (panelRight) {
        panelRight.classList.remove('hidden');
        resizeRight?.classList.remove('hidden');
      }

      // Show loading state
      const patientSummary = document.getElementById('patientSummary');
      const keyFindings = document.getElementById('keyFindings');
      const differentialDiagnosis = document.getElementById('differentialDiagnosis');
      const recommendedPlan = document.getElementById('recommendedPlan');
      const followUp = document.getElementById('followUp');

      if (patientSummary) patientSummary.innerHTML = '<em>Generating summary...</em>';
      if (keyFindings) keyFindings.innerHTML = '<li><span class="bullet">&bull;</span> Analyzing conversation...</li>';
      if (recommendedPlan) recommendedPlan.innerHTML = '<li><span class="check">&#10003;</span> Generating plan...</li>';

      const language = languageMode?.value || 'bilingual';
      const mode = chatMode?.value || 'real';

      const eventSource = new EventSource(
        `/agent_chat_stream?message=${encodeURIComponent('[Finalize]')}&lang=${language}&role=finalize&mode=${mode}`
      );

      eventSource.onmessage = (event) => {
        const item = JSON.parse(event.data);
        if (item.type === 'question_recommender') return;

        const role = (item.role || '').toLowerCase();
        const message = item.message || '';

        // Listener provides the summary
        if (role === 'listener') {
          if (patientSummary) {
            // Convert markdown bold **text** to <strong> and newlines to <br>
            // Put Swahili section in italics
            let formatted = message
              .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
              .replace(/\n/g, '<br>');

            // Wrap Swahili section in italics (text after "Swahili Summary")
            formatted = formatted.replace(
              /(<strong>Swahili Summary<\/strong>)(.*?)(?=<strong>|$)/gi,
              '$1<em>$2</em>'
            );
            patientSummary.innerHTML = formatted;
          }
          // Extract key findings from summary
          if (keyFindings) {
            const findings = [];
            const lines = message.split('\n').filter(l => l.trim().startsWith('-'));
            lines.slice(0, 5).forEach(line => {
              findings.push(`<li><span class="bullet">&bull;</span> ${line.replace(/^-\s*/, '')}</li>`);
            });
            if (findings.length > 0) {
              keyFindings.innerHTML = findings.join('');
            }
          }
        }

        // Clinician provides the final plan
        if (role === 'clinician') {
          if (recommendedPlan) {
            const planItems = [];
            const lines = message.split('\n').filter(l => l.trim().startsWith('-') || l.trim().match(/^Step\s*\d+/i));
            lines.forEach(line => {
              const cleanLine = line.replace(/^-\s*/, '').replace(/^Step\s*\d+:\s*/i, '');
              planItems.push(`<li><span class="check">&#10003;</span> ${cleanLine}</li>`);
            });
            if (planItems.length > 0) {
              recommendedPlan.innerHTML = planItems.join('');
            } else {
              recommendedPlan.innerHTML = `<li>${message.replace(/\n/g, '<br>')}</li>`;
            }
          }
          if (followUp) {
            followUp.innerHTML = 'Schedule follow-up appointment to review test results and discuss next steps.';
          }
          if (differentialDiagnosis) {
            differentialDiagnosis.innerHTML = '<li><span class="num">1.</span> See recommended plan for diagnostic approach</li>';
          }
        }
      };

      eventSource.onerror = () => {
        eventSource.close();
      };
    });
  }

  // Close Summary Panel
  if (closeSummary) {
    closeSummary.addEventListener('click', () => {
      panelRight?.classList.add('hidden');
      resizeRight?.classList.add('hidden');
    });
  }

  // Panel Resizing
  let isResizing = false;
  let resizingPanel = null;
  const panelLeft = document.getElementById('panel-left');
  const resizeLeft = document.getElementById('resize-left');

  if (resizeLeft) {
    resizeLeft.addEventListener('mousedown', (e) => {
      isResizing = true;
      resizingPanel = 'left';
      e.preventDefault();
    });
  }

  if (resizeRight) {
    resizeRight.addEventListener('mousedown', (e) => {
      isResizing = true;
      resizingPanel = 'right';
      e.preventDefault();
    });
  }

  document.addEventListener('mousemove', (e) => {
    if (!isResizing) return;

    if (resizingPanel === 'left' && panelLeft) {
      const newWidth = e.clientX - 12;
      if (newWidth >= 280 && newWidth <= 500) {
        panelLeft.style.width = newWidth + 'px';
      }
    } else if (resizingPanel === 'right' && panelRight) {
      const newWidth = window.innerWidth - e.clientX - 12;
      if (newWidth >= 250 && newWidth <= 450) {
        panelRight.style.width = newWidth + 'px';
      }
    }
  });

  document.addEventListener('mouseup', () => {
    isResizing = false;
    resizingPanel = null;
  });

  // Voice Recording
  let mediaRecorder;
  let audioChunks = [];
  const audioElement = document.getElementById("recordedAudio");

  if (recordAudioBtn) {
    recordAudioBtn.addEventListener("click", async () => {
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
            if (audioElement) {
              audioElement.src = URL.createObjectURL(audioBlob);
            }

            const formData = new FormData();
            formData.append("audio", audioBlob);
            formData.append("lang", languageMode?.value || 'bilingual');
            formData.append("role", currentRole);

            recordAudioBtn.innerHTML = '<i data-lucide="loader" style="width:16px;height:16px"></i><span>Transcribing...</span>';
            lucide?.createIcons();

            try {
              const response = await fetch("/transcribe_audio", {
                method: "POST",
                body: formData,
              });

              const data = await response.json();
              if (data.text && agentMessage) {
                agentMessage.value = data.text;
                sendMessage();
              } else {
                alert("Failed to transcribe audio.");
              }
            } catch (err) {
              alert("Transcription error.");
              console.error(err);
            }

            recordAudioBtn.innerHTML = '<i data-lucide="mic" style="width:16px;height:16px"></i><span>Voice</span>';
            recordAudioBtn.classList.remove('listening');
            lucide?.createIcons();
          };

          mediaRecorder.start();
          recordAudioBtn.innerHTML = '<i data-lucide="mic-off" style="width:16px;height:16px"></i><span>Stop</span>';
          recordAudioBtn.classList.add('listening');
          lucide?.createIcons();
        } catch (error) {
          alert("Microphone access denied.");
          console.error(error);
        }
      } else if (mediaRecorder.state === "recording") {
        mediaRecorder.stop();
      }
    });
  }

  // Initialize UI
  updateChatInputStyle();

  // ============================================
  // Export Functions (PDF & Word)
  // ============================================
  const exportPdfBtn = document.getElementById('exportPdfBtn');
  const exportWordBtn = document.getElementById('exportWordBtn');

  // Export to PDF
  if (exportPdfBtn) {
    exportPdfBtn.addEventListener('click', () => {
      const summaryContent = document.getElementById('summaryContent');
      if (!summaryContent) {
        alert('No summary content to export');
        return;
      }

      // Get current date for filename
      const date = new Date().toISOString().split('T')[0];
      const filename = `Medical_Report_${date}.pdf`;

      // Create a clone for PDF generation
      const element = summaryContent.cloneNode(true);
      element.style.padding = '20px';
      element.style.background = 'white';

      const opt = {
        margin: [10, 10, 10, 10],
        filename: filename,
        image: { type: 'jpeg', quality: 0.98 },
        html2canvas: { scale: 2, useCORS: true },
        jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
        pagebreak: { mode: 'avoid-all' }
      };

      // Show loading
      exportPdfBtn.disabled = true;
      exportPdfBtn.innerHTML = '<i data-lucide="loader" style="width:14px;height:14px"></i> Generating...';

      html2pdf().set(opt).from(summaryContent).save().then(() => {
        exportPdfBtn.disabled = false;
        exportPdfBtn.innerHTML = '<i data-lucide="file-text" style="width:14px;height:14px"></i> Export PDF';
        lucide?.createIcons();
      }).catch(err => {
        console.error('PDF export error:', err);
        exportPdfBtn.disabled = false;
        exportPdfBtn.innerHTML = '<i data-lucide="file-text" style="width:14px;height:14px"></i> Export PDF';
        lucide?.createIcons();
        alert('Failed to generate PDF');
      });
    });
  }

  // Export to Word
  if (exportWordBtn) {
    exportWordBtn.addEventListener('click', async () => {
      const patientSummary = document.getElementById('patientSummary')?.innerText || '';
      const keyFindings = document.getElementById('keyFindings')?.innerText || '';
      const differentialDiagnosis = document.getElementById('differentialDiagnosis')?.innerText || '';
      const recommendedPlan = document.getElementById('recommendedPlan')?.innerText || '';
      const followUp = document.getElementById('followUp')?.innerText || '';

      if (!patientSummary && !recommendedPlan) {
        alert('No summary content to export. Please generate a report first.');
        return;
      }

      // Show loading
      exportWordBtn.disabled = true;
      exportWordBtn.innerHTML = '<i data-lucide="loader" style="width:14px;height:14px"></i> Generating...';

      try {
        // docx library loaded from CDN as global
        const docxLib = window.docx;
        if (!docxLib) {
          throw new Error('Word export library not loaded');
        }
        const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType } = docxLib;

        const doc = new Document({
          sections: [{
            properties: {},
            children: [
              // Title
              new Paragraph({
                children: [
                  new TextRun({
                    text: "Medical Case Report",
                    bold: true,
                    size: 48,
                    color: "7bc148"
                  })
                ],
                heading: HeadingLevel.TITLE,
                alignment: AlignmentType.CENTER,
                spacing: { after: 400 }
              }),

              // Date
              new Paragraph({
                children: [
                  new TextRun({
                    text: `Report Date: ${new Date().toLocaleDateString()}`,
                    italics: true,
                    size: 22
                  })
                ],
                alignment: AlignmentType.CENTER,
                spacing: { after: 400 }
              }),

              // Patient Summary Section
              new Paragraph({
                children: [
                  new TextRun({ text: "Patient Summary", bold: true, size: 28, color: "333333" })
                ],
                heading: HeadingLevel.HEADING_1,
                spacing: { before: 300, after: 200 }
              }),
              new Paragraph({
                children: [new TextRun({ text: patientSummary || "No summary available", size: 22 })],
                spacing: { after: 300 }
              }),

              // Key Findings Section
              new Paragraph({
                children: [
                  new TextRun({ text: "Key Findings", bold: true, size: 28, color: "f97316" })
                ],
                heading: HeadingLevel.HEADING_1,
                spacing: { before: 300, after: 200 }
              }),
              new Paragraph({
                children: [new TextRun({ text: keyFindings || "No findings recorded", size: 22 })],
                spacing: { after: 300 }
              }),

              // Differential Diagnosis Section
              new Paragraph({
                children: [
                  new TextRun({ text: "Differential Diagnosis", bold: true, size: 28, color: "333333" })
                ],
                heading: HeadingLevel.HEADING_1,
                spacing: { before: 300, after: 200 }
              }),
              new Paragraph({
                children: [new TextRun({ text: differentialDiagnosis || "Pending analysis", size: 22 })],
                spacing: { after: 300 }
              }),

              // Recommended Plan Section
              new Paragraph({
                children: [
                  new TextRun({ text: "Recommended Plan", bold: true, size: 28, color: "7bc148" })
                ],
                heading: HeadingLevel.HEADING_1,
                spacing: { before: 300, after: 200 }
              }),
              new Paragraph({
                children: [new TextRun({ text: recommendedPlan || "No plan generated", size: 22 })],
                spacing: { after: 300 }
              }),

              // Follow-up Instructions Section
              new Paragraph({
                children: [
                  new TextRun({ text: "Follow-up Instructions", bold: true, size: 28, color: "333333" })
                ],
                heading: HeadingLevel.HEADING_1,
                spacing: { before: 300, after: 200 }
              }),
              new Paragraph({
                children: [new TextRun({ text: followUp || "No follow-up instructions", size: 22 })],
                spacing: { after: 300 }
              }),

              // Footer
              new Paragraph({
                children: [
                  new TextRun({
                    text: "Generated by Early Cancer Diagnosis System - Kenya",
                    italics: true,
                    size: 18,
                    color: "888888"
                  })
                ],
                alignment: AlignmentType.CENTER,
                spacing: { before: 600 }
              })
            ]
          }]
        });

        const blob = await Packer.toBlob(doc);
        const date = new Date().toISOString().split('T')[0];
        saveAs(blob, `Medical_Report_${date}.docx`);

      } catch (err) {
        console.error('Word export error:', err);
        alert('Failed to generate Word document');
      }

      exportWordBtn.disabled = false;
      exportWordBtn.innerHTML = '<i data-lucide="file-down" style="width:14px;height:14px"></i> Export Word';
      lucide?.createIcons();
    });
  }
});

// ============================================
// CSRF Token Management
// ============================================
let CSRF_TOKEN = null;

async function loadCsrf() {
  const r = await fetch('/csrf-token', { credentials: 'same-origin' });
  const j = await r.json();
  CSRF_TOKEN = j.csrfToken;
  window.CSRF_TOKEN = CSRF_TOKEN;
}

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-CSRFToken': CSRF_TOKEN || ''
  };
}

// ============================================
// Auth API
// ============================================
async function getMe() {
  const r = await fetch('/auth/me', { credentials: 'same-origin' });
  return r.json();
}

async function login(email, password, remember = true) {
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

// ============================================
// Auth UI
// ============================================
function showAuth() {
  const authGate = document.getElementById('auth-gate');
  const appContainer = document.getElementById('app-container');
  if (authGate) authGate.style.display = 'flex';
  if (appContainer) appContainer.style.display = 'none';
  showLoginCard();
}

function showApp() {
  const authGate = document.getElementById('auth-gate');
  const appContainer = document.getElementById('app-container');
  if (authGate) authGate.style.display = 'none';
  if (appContainer) appContainer.style.display = 'flex';
}

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

// Auth toggle buttons
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-action="show-signup"], [data-action="show-login"]');
  if (!el) return;
  e.preventDefault();
  if (el.dataset.action === 'show-signup') showSignupCard();
  if (el.dataset.action === 'show-login') showLoginCard();
});

// Auth forms
window.addEventListener('DOMContentLoaded', async () => {
  await loadCsrf();
  const me = await getMe();

  if (me.authenticated) {
    showApp();
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
        location.reload();
      } else {
        const errEl = document.getElementById('auth-error');
        errEl.textContent = res.error || 'Login failed';
        errEl.classList.remove('d-none');
        await loadCsrf();
      }
    } catch {
      const errEl = document.getElementById('auth-error');
      errEl.textContent = 'Network error';
      errEl.classList.remove('d-none');
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
        const res2 = await login(email, password);
        if (res2.ok) {
          location.reload();
        }
      } else {
        const errEl = document.getElementById('auth-error');
        errEl.textContent = res.error || 'Signup failed';
        errEl.classList.remove('d-none');
        await loadCsrf();
      }
    } catch {
      const errEl = document.getElementById('auth-error');
      errEl.textContent = 'Network error';
      errEl.classList.remove('d-none');
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

// ============================================
// Live Mode (WebSocket STT)
// ============================================
let liveMediaStream = null, liveRecorder = null, liveWS = null, liveActive = false;
let lastFinalText = "", lastFinalAt = 0;

function wsURL(path) {
  const base = new URL(window.location.origin);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${base.origin}${path.startsWith('/') ? path : '/' + path}`;
}

function getLiveRecoMode() {
  return document.getElementById('liveRecoMode')?.value === 'unasked' ? 'unasked' : 'normal';
}

async function startLive() {
  if (document.getElementById('chatMode')?.value !== 'live') {
    alert('Switch Mode to "Live (Mic)" first.');
    return;
  }
  if (liveActive) return;
  liveActive = true;

  const mime = 'audio/webm;codecs=opus';
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported(mime)) {
    alert('Your browser does not support WebM/Opus recording.');
    liveActive = false;
    return;
  }

  try {
    liveMediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, noiseSuppression: true, echoCancellation: true, autoGainControl: true }
    });
  } catch {
    alert('Microphone access denied.');
    liveActive = false;
    return;
  }

  const uiLang = document.getElementById('languageMode')?.value || 'bilingual';
  const lang = (uiLang === 'english') ? 'en' : (uiLang === 'swahili' ? 'sw' : 'bilingual');

  liveWS = new WebSocket(wsURL(`/ws/stt?lang=${encodeURIComponent(lang)}`));
  liveWS.binaryType = 'arraybuffer';

  const liveStatus = document.getElementById('liveStatus');
  const liveLineBox = document.getElementById('liveLineBox');
  const liveIndicator = document.getElementById('liveIndicator');
  const liveMessage = document.getElementById('liveMessage');

  liveWS.onopen = () => {
    if (liveStatus) liveStatus.textContent = 'listening...';
    liveLineBox?.classList.add('active');
    liveIndicator?.classList.remove('hidden');
    if (liveMessage) liveMessage.textContent = 'Listening for speech...';
  };
  liveWS.onerror = () => { if (liveStatus) liveStatus.textContent = 'connection error'; };
  liveWS.onclose = () => { if (liveStatus) liveStatus.textContent = ''; };

  liveWS.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }

    if (msg.type === 'partial') {
      if (liveMessage) liveMessage.textContent = msg.text || 'Listening...';
      return;
    }

    if (msg.type === 'final') {
      const text = (msg.text || '').trim();
      if (!text) return;
      const now = Date.now();
      if (text === lastFinalText && (now - lastFinalAt) < 2000) return;
      lastFinalText = text;
      lastFinalAt = now;

      // Add to transcript
      const transcriptEl = document.getElementById('agentChatTranscript');
      if (transcriptEl) {
        const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const msgEl = document.createElement('div');
        msgEl.className = 'message patient';
        msgEl.innerHTML = `
          <div class="message-header">
            <span class="message-role patient">Speaker</span>
            <span class="message-time patient">${ts}</span>
          </div>
          <p class="message-text">${text.replace(/\n/g, '<br>')}</p>
        `;
        transcriptEl.appendChild(msgEl);
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
      }

      // Get recommendations
      const es = new EventSource(`/agent_chat_stream?message=${encodeURIComponent(text)}&lang=${encodeURIComponent(uiLang)}&role=patient&mode=live`);
      es.onmessage = (event) => {
        let item;
        try { item = JSON.parse(event.data); } catch { return; }
        if ((item.role || '').toLowerCase() === 'patient') return;

        if (item.type === 'question_recommender' && getLiveRecoMode() === 'normal') {
          const englishText = item.question?.english || '';
          const swahiliText = item.question?.swahili || '';
          const questionText = englishText || swahiliText;
          const isSwahili = !englishText && swahiliText;

          // Always store the suggestion (with Swahili flag)
          window.lastSuggestion = { text: questionText, isSwahili };

          // Only show if Clinician tab is active
          const isClinicianActive = document.getElementById('roleClinicianBtn')?.classList.contains('active-clinician');
          if (!isClinicianActive) return;

          const questionTextEl = document.getElementById('suggestionText');
          const suggestionBoxEl = document.getElementById('suggestionBox');

          if (questionText && questionTextEl && suggestionBoxEl) {
            // Swahili in italics
            questionTextEl.innerHTML = isSwahili ? `<em>${questionText}</em>` : questionText;
            suggestionBoxEl.classList.remove('hidden');
          }
        }
      };
      es.onerror = () => es.close();
      setTimeout(() => es.close(), 8000);
    }
  };

  liveRecorder = new MediaRecorder(liveMediaStream, { mimeType: mime, audioBitsPerSecond: 32000 });
  liveRecorder.addEventListener('dataavailable', (evt) => {
    if (!liveActive || !evt.data || !evt.data.size) return;
    if (liveWS?.readyState === WebSocket.OPEN) {
      evt.data.arrayBuffer().then(buf => { try { liveWS.send(buf); } catch {} });
    }
  });
  liveRecorder.start(250);

  const startBtn = document.getElementById('startLiveBtn');
  const stopBtn = document.getElementById('stopLiveBtn');
  if (startBtn) startBtn.disabled = true;
  if (stopBtn) stopBtn.disabled = false;
}

async function stopLive() {
  liveActive = false;
  try { liveRecorder?.requestData?.(); } catch {}
  await new Promise(r => setTimeout(r, 60));
  try {
    if (liveRecorder && liveRecorder.state !== 'inactive') {
      await new Promise(res => { liveRecorder.addEventListener('stop', res, { once: true }); liveRecorder.stop(); });
    }
  } catch {}
  try { liveMediaStream?.getTracks().forEach(t => t.stop()); } catch {}
  try { liveWS?.close(); } catch {}

  const startBtn = document.getElementById('startLiveBtn');
  const stopBtn = document.getElementById('stopLiveBtn');
  const liveLineBox = document.getElementById('liveLineBox');
  const liveIndicator = document.getElementById('liveIndicator');
  const liveMessage = document.getElementById('liveMessage');
  const liveStatus = document.getElementById('liveStatus');

  if (startBtn) startBtn.disabled = false;
  if (stopBtn) stopBtn.disabled = true;
  if (liveStatus) liveStatus.textContent = '';
  liveLineBox?.classList.remove('active');
  liveIndicator?.classList.add('hidden');
  if (liveMessage) liveMessage.textContent = 'Partials appear here.';

  liveMediaStream = null;
  liveRecorder = null;
  liveWS = null;
}

document.addEventListener('click', (e) => {
  if (e.target?.id === 'startLiveBtn') startLive();
  if (e.target?.id === 'stopLiveBtn') stopLive();
});

document.getElementById('chatMode')?.addEventListener('change', (e) => {
  if (e.target.value !== 'live' && liveActive) stopLive();
});

// ============================================
// Patient Management Functions
// ============================================
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

async function loadPatients() {
  const sel = document.getElementById('patientSelect');
  if (!sel) return;
  const currentVal = sel.value;
  sel.innerHTML = '<option value="">-- No Patient --</option>';
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

// Wire patient selector change event
document.getElementById('patientSelect')?.addEventListener('change', async (e) => {
  await syncSessionPatient(e.target.value || null);
});

// Initialize patient management on page load
window.addEventListener('DOMContentLoaded', () => {
  loadPatients();
  wireNewPatientButton();
});
