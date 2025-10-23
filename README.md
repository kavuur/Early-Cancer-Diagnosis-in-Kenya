# Cancer Diagnostic Assistant (Agentic AI for Early Cancer Detection)

### Overview

The **Cancer Diagnostic Assistant** is a digital tool powered by **agentic AI** that supports **general practitioners (GPs)** in conducting effective clinical interviews for suspected cancer cases. The system guides clinicians to ask all **relevant, evidence-based questions**, ensuring that **early warning signs** of common cancers are not missed before referral to specialists.

This project aims to **reduce diagnostic delays**, improve **primary-care decision support**, and ultimately **enhance early cancer detection outcomes** in low-resource settings.

---

### Core Features

* 🧠 **Multi-Agent Intelligence:**
  A coordinated set of AI agents (Clinician, Patient, Listener, Question Recommender) simulate or support real diagnostic conversations.

* 💬 **Natural-Language Dialogue:**
  Enables bilingual (English–Swahili) conversational screening between clinician and patient through voice and text.

* 🔎 **Question Recommendation Engine:**
  Dynamically suggests the next best clinical question based on the ongoing conversation and FAISS-retrieved data from a question knowledge base.

* 🔊 **Real-Time Speech-to-Text (STT):**
  Integrates local **faster-whisper** and **Jacaranda ASR** models for offline and low-bandwidth transcription, with automatic switching by language context.

* 🧩 **Adaptive Diagnostic Reasoning:**
  Continuously interprets dialogue to estimate likelihoods of key symptom clusters linked to **early cancer indicators**.

* 🧍 **Real / Simulated Patient Modes:**
  Can be used for live clinician-patient interviews or simulated training sessions for clinical education.

---

### System Architecture

The system combines:

* **Flask backend** with WebSocket streaming for live transcription and real-time agent orchestration.
* **CrewAI orchestration layer** managing agent roles (`clinician_agent`, `patient_agent`, `listener_agent`, `question_recommender_agent`).
* **FAISS retrieval engine** for question and knowledge indexing.
* **Frontend (HTML/JS/CSS)** with modern chat interface and live audio capture.

---

### Technical Stack

* **Backend:** Python (Flask), FAISS, CrewAI
* **Frontend:** JavaScript (WebSocket + SSE), HTML5, CSS3
* **Speech-to-Text:** faster-whisper, Jacaranda ASR
* **Embedding Model:** all-MiniLM-L6-v2
* **LLM Interface:** GPT-4 / Llama-3 (configurable)

---

### Current Status

* ✅ MVP **fully developed and operational**
* 🧪 **Awaiting trial testing** in real-world primary-care settings
* 🔄 Continuous optimization for speed, accuracy, and context adaptation

---

### Future Work

* Integration with electronic medical records (EMR)
* Support for additional African languages
* Deployment on local hospital networks for offline functionality
* Evaluation and fine-tuning using real clinician–patient data

---

### Contributors

Developed by **Dr. Tatenda Duncan Kavu** and collaborators at the **African Population and Health Research Center (APHRC)**, within the **Data Science Program**.
Special thanks to research partners and medical practitioners contributing to the pilot testing phase.

---

### License

This project is released under the **MIT License**. See `LICENSE` for details.
