# ---------------------------------------------------------------------------
# FIX: Disable CrewAI telemetry BEFORE importing crewai.
# Without this, every crew.kickoff() spawns a background thread that tries
# to POST traces to telemetry.crewai.com. When that host is unreachable the
# thread blocks for ~30 s then dumps a giant ConnectTimeout traceback to the
# log. Setting both env-vars covers older and newer CrewAI versions.
# ---------------------------------------------------------------------------
import os
os.environ.setdefault("CREWAI_TELEMETRY", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import Crew, Task
from agent_loader import load_llm, load_agents_from_yaml, load_tasks_from_yaml
from datetime import datetime
import json
import re
import time
import logging
from typing import List, Dict, Any
from difflib import SequenceMatcher

from medical_case_faiss import MedicalCaseFAISS

logger = logging.getLogger(__name__)

AGENT_PATH = 'config/agents.yaml'
TASK_PATH = 'config/tasks.yaml'

# ---------------------------------------------------------------------------
# FIX #3: Lazy FAISS initialization — do NOT load at module import time.
# Previously the top-level `faiss_system.load_index(...)` call caused the
# entire app to crash on startup if the index files were missing, and it
# also hardcoded the paths instead of reading from env/config.
# ---------------------------------------------------------------------------
_faiss_system = None


def _get_faiss() -> MedicalCaseFAISS:
    """Return the shared FAISS instance, initializing it on first call."""
    global _faiss_system
    if _faiss_system is None:
        idx_path = os.getenv("FAISS_INDEX_PATH", "medical_cases.index")
        meta_path = os.getenv("FAISS_METADATA_PATH", "medical_cases_metadata.pkl")
        _faiss_system = MedicalCaseFAISS()
        _faiss_system.load_index(idx_path, meta_path)
        logger.info(f"FAISS loaded in crew_runner: index={idx_path}, meta={meta_path}")
    return _faiss_system


# ---------------------------- NEW: Listener bundle (Live Stop) ----------------------------

def build_listener_bundle(convo_text: str, language_mode: str = "bilingual") -> str:
    """
    Live mic mode has *real* clinicians, so we don't generate a clinician final plan.
    Instead, we ask the listener agent to produce:
      - English Summary (bullet points)
      - Swahili Summary (bullet points)
      - FINAL PLAN (bullet steps)

    Returns a single markdown-ish string that the UI can render.
    """
    llm = load_llm()
    agents = load_agents_from_yaml(AGENT_PATH, llm)
    listener = agents.get("listener_agent")
    if not listener:
        raise RuntimeError("listener_agent not found in agents.yaml")

    convo_text = (convo_text or "").strip()
    convo_clip = convo_text[-9000:] if len(convo_text) > 9000 else convo_text

    if language_mode == "swahili":
        instruction = (
            "Andika muhtasari wa mazungumzo haya kwa Kiswahili (vipengele vya nukta), "
            "kisha toa mpango wa hatua kwa hatua wa nini kinachofuata kliniki. "
            "Fuata muundo HUU hasa:\n\n"
            "Listener:\n"
            "**Swahili Summary:**\n"
            "- ...\n\n"
            "**FINAL PLAN:**\n"
            "- Step 1: ...\n"
            "- Step 2: ...\n\n"
            "Weka kwa ufupi, wa kitabibu, na wa vitendo."
        )
    elif language_mode == "english":
        instruction = (
            "Summarize this medical conversation in English (bullet points), then provide "
            "a practical step-by-step clinical plan. "
            "Follow THIS exact structure:\n\n"
            "Listener:\n"
            "**English Summary:**\n"
            "- ...\n\n"
            "**FINAL PLAN:**\n"
            "- Step 1: ...\n"
            "- Step 2: ...\n\n"
            "Keep it concise, medical, and actionable."
        )
    else:
        instruction = (
            "Summarize this medical conversation in two parts (English + Swahili) using bullet points, "
            "then provide a practical step-by-step clinical plan. "
            "Follow THIS exact structure (no extra text):\n\n"
            "Listener:\n"
            "**English Summary:**\n"
            "- ...\n\n"
            "**Swahili Summary:**\n"
            "- ...\n\n"
            "**FINAL PLAN:**\n"
            "- Step 1: ...\n"
            "- Step 2: ...\n"
            "- Step 3: ...\n\n"
            "Make the plan clinically sensible (tests, referral, follow-up) and keep it short."
        )

    prompt = f"Conversation transcript:\n{convo_clip}\n\n{instruction}"
    return run_task(listener, prompt, name="Listener Summary + Final Plan")


# ---------------------------- NEW HELPERS (Live Unasked) ----------------------------

def normalize_text(text: str) -> str:
    """Normalize text for matching: lowercase, remove punctuation-ish, collapse whitespace."""
    if not text:
        return ""
    t = text.lower().strip()
    t = re.sub(r"[\r\n\t]+", " ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _safe_json_from_text(text: str) -> Any:
    """Try hard to parse JSON from model output."""
    if not text:
        return None
    # Find first JSON array/object in the response
    m = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    candidate = m.group(1) if m else text
    try:
        return json.loads(candidate)
    except Exception:
        return None


def questions_are_similar(q1: str, q2: str, threshold: float = 0.75) -> bool:
    """
    Check if two questions are semantically similar using:
    1. Token overlap ratio
    2. Sequence similarity
    Returns True if they're likely asking the same thing.
    """
    norm1 = normalize_text(q1)
    norm2 = normalize_text(q2)

    if not norm1 or not norm2:
        return False

    tokens1 = set(norm1.split())
    tokens2 = set(norm2.split())

    if not tokens1 or not tokens2:
        return False

    overlap = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    token_similarity = overlap / union if union > 0 else 0.0

    seq_similarity = SequenceMatcher(None, norm1, norm2).ratio()

    combined_score = (token_similarity * 0.4) + (seq_similarity * 0.6)

    return combined_score >= threshold


def deduplicate_questions(questions: List[str]) -> List[str]:
    """
    Remove semantically duplicate questions, keeping the first occurrence.
    """
    unique = []
    for q in questions:
        is_duplicate = False
        for existing in unique:
            if questions_are_similar(q, existing):
                is_duplicate = True
                break
        if not is_duplicate:
            unique.append(q)
    return unique


def is_coherent_medical_text(text: str, conversation_context: str = "") -> bool:
    """
    Validate if transcribed text is coherent and relevant to medical conversation.
    Returns False for hallucinations, noise, or gibberish.
    """
    if not text or len(text.strip()) < 3:
        return False

    text_lower = text.lower().strip()

    hallucination_patterns = [
        r'^(um+|uh+|hmm+|ah+|oh+)$',
        r'^(okay|ok|yeah|yes|no|maybe)$',
        r'(thank you for watching|subscribe|like and subscribe)',
        r'(subtitles|captions|music|applause|laughter)',
        r'^[\W_]+$',
        r'(.)\1{4,}',
        # FIX: Catch common STT mic-test phrases that pollute the conversation context
        r'^(one[\s,]*two[\s,]*three|testing[\s,]*one|check[\s,]*one|mic[\s,]*check)',
        r'^(test(ing)?[\s,]*\d+)',
        r'(one two three|1 2 3|testing testing)',
    ]

    for pattern in hallucination_patterns:
        if re.search(pattern, text_lower):
            return False

    if len(text.strip()) < 5 and text.strip().isdigit():
        return False

    medical_keywords = [
        'pain', 'ache', 'hurt', 'feel', 'symptom', 'sick', 'ill', 'doctor', 'hospital',
        'medicine', 'treatment', 'diagnosis', 'test', 'exam', 'blood', 'pressure',
        'headache', 'fever', 'cough', 'breath', 'chest', 'stomach', 'back', 'leg', 'arm',
        'week', 'month', 'day', 'year', 'ago', 'started', 'began', 'worse', 'better',
        'maumivu', 'homa', 'kichwa', 'kifua', 'tumbo', 'mguu', 'mkono', 'daktari',
        'hospitali', 'dawa', 'matibabu', 'ugonjwa', 'dalili', 'kipimo'
    ]

    if len(text.split()) >= 3:
        has_medical_keyword = any(keyword in text_lower for keyword in medical_keywords)

        conversational_phrases = [
            'i have', 'i feel', 'it started', 'it hurts', 'when i', 'how long',
            'what about', 'can you', 'could you', 'should i', 'is it',
            'nina', 'nimehisi', 'inauma', 'tangu', 'wiki', 'siku'
        ]
        has_conversational = any(phrase in text_lower for phrase in conversational_phrases)

        if not (has_medical_keyword or has_conversational):
            if conversation_context and len(conversation_context) > 100:
                return False

    return True


def rank_questions_for_unasked(
    convo_text: str,
    questions: List[str],
    language_mode: str = "bilingual"
) -> List[Dict[str, Any]]:
    """
    Return list of {question, score} sorted desc.
    - Enhanced with medical relevance focus
    - Limits to top 5-10 most critical questions
    - Removes duplicates
    """
    questions = [q.strip() for q in (questions or []) if (q or "").strip()]
    if not questions:
        return []

    questions = deduplicate_questions(questions)

    convo_text = (convo_text or "").strip()
    convo_clip = convo_text[-6000:] if len(convo_text) > 6000 else convo_text

    try:
        llm = load_llm()
        agents = load_agents_from_yaml(AGENT_PATH, llm)
        scorer = agents.get("clinician_agent") or agents.get("question_recommender_agent")

        if scorer:
            q_list = "\n".join([f"- {q}" for q in questions])
            if language_mode == "swahili":
                instruction = (
                    "Tathmini maswali yafuatayo kwa umuhimu wake wa msingi katika kuchunguza saratani (0 hadi 1). "
                    "MUHIMU: Toa alama ya juu zaidi kwa maswali ambayo:\n"
                    "1. Yanaweza kufichua dalili za saratani zinazobadilisha maisha (red flags)\n"
                    "2. Yangepunguzwa mgonjwa anaweza kuwa na uchunguzi usio sahihi wa saratani\n"
                    "3. Yanahitaji kuulizwa sasa ili kupata historia ya kutosha ya matibabu\n\n"
                    "Toa JSON pekee: [{\"question\":\"...\",\"score\":0.0,\"rationale\":\"...\"}, ...]. "
                    "Panga kwa score kubwa kwenda ndogo. Weka maswali 5-10 ya juu PEKEE. Hakuna maelezo mengine."
                )
            else:
                instruction = (
                    "Score the following questions by their CRITICAL IMPORTANCE for cancer diagnosis (0 to 1). "
                    "IMPORTANT: Give highest scores to questions that:\n"
                    "1. Could reveal life-changing cancer red flags or symptoms\n"
                    "2. If not asked, the patient might be misdiagnosed or cancer missed\n"
                    "3. Are essential for establishing proper medical history NOW\n\n"
                    "Return ONLY JSON in this exact format: "
                    "[{\"question\":\"...\",\"score\":0.0,\"rationale\":\"why this is critical\"}, ...]. "
                    "Sort by descending score. Include ONLY the top 5-10 most critical questions. No extra text."
                )

            prompt = (
                f"Medical conversation context (most recent):\n{convo_clip}\n\n"
                f"Questions to evaluate for critical diagnostic importance:\n{q_list}\n\n"
                f"{instruction}"
            )

            scored_text = run_task(scorer, prompt, name="Critical Question Scoring")
            parsed = _safe_json_from_text(scored_text)

            if isinstance(parsed, list):
                out = []
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    q = (item.get("question") or "").strip()
                    if not q:
                        continue
                    s = item.get("score")
                    try:
                        s = float(s)
                    except Exception:
                        s = 0.0
                    if s < 0.0:
                        s = 0.0
                    if s > 1.0:
                        s = 1.0
                    out.append({"question": q, "score": s})

                orig_norm = {normalize_text(q): q for q in questions}
                filtered = []
                for row in out:
                    nq = normalize_text(row["question"])
                    if nq in orig_norm:
                        filtered.append({"question": orig_norm[nq], "score": float(row["score"])})

                if filtered:
                    filtered.sort(key=lambda x: x["score"], reverse=True)
                    high_priority = [q for q in filtered if q["score"] >= 0.6]
                    if len(high_priority) < 5:
                        return filtered[:10]
                    elif len(high_priority) > 10:
                        return filtered[:10]
                    else:
                        return high_priority
    except Exception:
        logger.exception("LLM scoring failed; falling back to heuristic ranking")

    # ---------------- fallback heuristic ranking ----------------
    conv_norm = normalize_text(convo_text)
    conv_tokens = set(conv_norm.split())

    ranked = []
    for q in questions:
        qn = normalize_text(q)
        q_tokens = set(qn.split())
        if not q_tokens:
            score = 0.0
        else:
            overlap = len(q_tokens & conv_tokens)
            score = overlap / max(1, len(q_tokens))
        ranked.append({"question": q, "score": float(score)})

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:10]


# ---------------------------------------------------------------------------
# FIX #4: Live recommender throttle — server-side timestamp tracking.
#
# The old implementation called `_recent_recommender_emitted(history, ...)`,
# which inspected `conversation_history` (session["conv"]) for entries with
# type == "question_recommender". Those entries are NEVER stored in
# session["conv"] — only plain messages are — so the check always returned
# False and the throttle was permanently bypassed, flooding the UI with
# recommendations on every transcription chunk.
#
# Fix: track the last emission time on a module-level dict keyed by
# (user_id, conversation_id), passed in from app.py via live state.
# The live state dict already exists per-session in app.py; we expose a
# helper here that the streaming functions use to read/write it.
# ---------------------------------------------------------------------------

LIVE_RECO_MIN_INTERVAL_SEC = 7


def should_throttle_reco(live_state: dict, lock) -> bool:
    """
    Return True if a recommendation was emitted within the throttle window.
    `live_state` is the per-session dict from app.py's LIVE_STATE.
    `lock` is the LIVE_STATE_LOCK RLock from app.py.
    """
    with lock:
        last_ts = live_state.get("last_reco_ts", 0.0)
    return (time.time() - last_ts) < float(LIVE_RECO_MIN_INTERVAL_SEC)


def record_reco_emitted(live_state: dict, lock) -> None:
    """Mark that a recommendation was just emitted (update timestamp)."""
    with lock:
        live_state["last_reco_ts"] = time.time()


# ---------------------------- EXISTING CORE ----------------------------

def run_task(agent, input_text, name="Step"):
    crew = Crew(
        agents=[agent],
        tasks=[Task(
            name=name,
            description=input_text,
            expected_output="Give your response as if you were in the middle of the diagnostic session.",
            agent=agent
        )],
        verbose=False
    )
    result = crew.kickoff()
    return result if isinstance(result, str) else getattr(result, 'final_output', str(result))


# Helpers
def format_event(role, message):
    return "data: " + json.dumps({
        "role": role,
        "message": (message or "").strip(),
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }) + "\n\n"


def format_event_recommender(english, swahili):
    return "data: " + json.dumps({
        "type": "question_recommender",
        "question": {
            "english": (english or "").strip(),
            "swahili": (swahili or "").strip()
        },
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    }) + "\n\n"


def format_bilingual(english: str, swahili: str) -> str:
    return f"<strong>English:</strong><br>{english or '—'}<br><br><strong>Swahili:</strong><br>{swahili or '—'}"


def sse_message(role, message, log_hook=None, session_id=None):
    ts = datetime.now().strftime("%H:%M:%S")
    payload = {"role": role, "message": (message or "").strip(), "timestamp": ts}
    if log_hook:
        log_hook(session_id, role, payload["message"], ts, "message")
    return "data: " + json.dumps(payload) + "\n\n"


def sse_recommender(english, swahili, log_hook=None, session_id=None):
    ts = datetime.now().strftime("%H:%M:%S")
    payload = {
        "type": "question_recommender",
        "question": {"english": (english or "").strip(), "swahili": (swahili or "").strip()},
        "timestamp": ts,
    }
    if log_hook:
        msg = f"Recommended Q | EN: {payload['question']['english']} | SW: {payload['question']['swahili']}"
        log_hook(session_id, "Question Recommender", msg, ts, "question_recommender")
    return "data: " + json.dumps(payload) + "\n\n"


# Mode 1: Fully simulated
def simulate_agent_chat_stepwise(
    initial_message: str,
    turns: int = 6,
    language_mode: str = 'bilingual',
    log_hook=None,
    session_id=None
):
    llm = load_llm()
    agents = load_agents_from_yaml(AGENT_PATH, llm)

    # FIRST: yield the patient's seed so we always log at least one row
    yield sse_message("Patient", initial_message, log_hook, session_id)

    # THEN: try retrieval; if it fails, continue gracefully
    similar_cases = []
    similar_bullets = ""
    try:
        similar_cases = _get_faiss().search_similar_cases(initial_message, k=5, similarity_threshold=0.19) or []
        similar_bullets = "\n".join(
            f"- {getattr(c, 'title', 'Case')}: {getattr(c, 'summary', '')}" for c in similar_cases
        )
    except Exception:
        logger.exception("FAISS search failed during simulated mode; continuing without retrieval")

    context_log = [f"Patient says: {initial_message}"]
    if similar_bullets:
        context_log.append("Similar cases (context):\n" + similar_bullets)

    for turn in range(turns):
        if language_mode == "english":
            recommender_input = "\n".join(
                context_log) + "\n\nSuggest the next most relevant diagnostic question. Format: English: ..."
        elif language_mode == "swahili":
            recommender_input = "\n".join(
                context_log) + "\n\nPendekeza swali fupi la uchunguzi linalofuata. Format: Swahili: ..."
        else:
            recommender_input = "\n".join(
                context_log) + "\n\nSuggest the next most relevant bilingual question only. Format as:\nEnglish: ...\n\nSwahili: ..."

        recommended = run_task(
            agents["question_recommender_agent"],
            recommender_input,
            f"Question Suggestion {turn + 1}"
        )

        if language_mode == "english":
            english_q, swahili_q = recommended.strip(), ""
        elif language_mode == "swahili":
            english_q, swahili_q = "", recommended.strip()
        else:
            match = re.search(r"English:\s*(.+?)\n+Swahili:\s*(.+)", recommended, re.DOTALL)
            if match:
                english_q, swahili_q = match.group(1).strip(), match.group(2).strip()
            else:
                english_q, swahili_q = recommended.strip(), ""

        if language_mode == "english":
            plain_q = f"{english_q}"
        elif language_mode == "swahili":
            plain_q = f"{swahili_q}"
        else:
            plain_q = f"{english_q}\n\n{swahili_q}"

        yield sse_recommender(english_q, swahili_q, log_hook, session_id)
        yield sse_message("Clinician", plain_q, log_hook, session_id)
        context_log.append(f"Clinician: {plain_q}")

        if language_mode == "english":
            patient_input = f"Clinician: {english_q}\n\nRespond in English as the patient. Be short and realistic."
        elif language_mode == "swahili":
            patient_input = f"Clinician: {swahili_q}\n\nJibu kwa Kiswahili kama mgonjwa. Toa jibu fupi na halisi."
        else:
            patient_input = f"Clinician: English: {english_q} Swahili: {swahili_q}\n\nRespond as the patient. Answer both languages if possible. Be short and realistic."

        patient_response = run_task(agents["patient_agent"], patient_input, f"Patient Response {turn + 1}")
        yield sse_message("Patient", patient_response, log_hook, session_id)
        context_log.append(f"Patient: {patient_response}")

    # Finalize
    listener_input = "\n".join(
        context_log) + "\n\nSummarize the conversation in two parts:\n**English Summary:**\n- ...\n**Swahili Summary:**\n- ..."
    listener_summary = run_task(agents["listener_agent"], listener_input, "Listener Summary")
    yield sse_message("Listener", listener_summary, log_hook, session_id)

    final_input = listener_input + "\n\nProvide a FINAL PLAN clearly structured as bullet points. Format like:\n**FINAL PLAN:**\n- Step 1: ...\n- Step 2: ..."
    final_plan = run_task(agents["clinician_agent"], final_input, "Final Plan")
    yield sse_message("Clinician", f"**FINAL PLAN:**\n\n{final_plan}", log_hook, session_id)


# Mode 2: Real actors
def real_actor_chat_stepwise(
    initial_message: str,
    language_mode: str = 'bilingual',
    speaker_role: str = 'Patient',
    conversation_history: list | None = None,
    log_hook=None,
    session_id=None
):
    """
    Real-actors mode with coherence validation.
    - Patient message triggers Question recommender
    - Finalize produces Summary + Final Plan
    """
    llm = load_llm()
    agents = load_agents_from_yaml(AGENT_PATH, llm)
    history = conversation_history or []

    # Finalize
    if speaker_role.lower() == "finalize":
        transcript_lines = [f"{m.get('role', '')}: {m.get('message', '')}" for m in history]
        convo_text = "\n".join(transcript_lines)

        listener_input = convo_text + "\n\nSummarize the conversation in two parts:\n**English Summary:**\n ...\n**Swahili Summary:**\n ..."
        listener_summary = run_task(agents["listener_agent"], listener_input, "Listener Summary")
        yield sse_message("Listener", listener_summary, log_hook, session_id)

        final_input = listener_input + "\n\nProvide a FINAL PLAN clearly structured as bullet points. Format like:\n**FINAL PLAN:**\n- Step 1: ...\n- Step 2: ..."
        final_plan = run_task(agents["clinician_agent"], final_input, "Final Plan")
        yield sse_message("Clinician", f"**FINAL PLAN**\n\n{final_plan}", log_hook, session_id)
        return

    context_lines = [f"{m.get('role')}: {m.get('message')}" for m in history[-10:]]
    context_text = "\n".join(context_lines)

    # FIX: Validate the incoming message BEFORE building the full context that
    # gets sent to the LLM. This stops STT noise (mic tests, garbled audio,
    # one-word utterances) from polluting the agent's task description.
    # Note: this check runs on the *raw* incoming message, not the history,
    # so it catches noise that the caller may have already appended to history.
    if not is_coherent_medical_text(initial_message, context_text):
        logger.info(f"Filtered incoherent text: {initial_message}")
        return

    yield sse_message(speaker_role, initial_message, log_hook, session_id)

    lower_role = speaker_role.lower()
    if lower_role in ("patient", "clinician"):
        context_lines = [f"{m.get('role')}: {m.get('message')}" for m in history]
        context_text = "\n".join(context_lines)

        if language_mode == "english":
            recommender_input = context_text + "\n\nSuggest the next most relevant diagnostic question. Format: English: ..."
        elif language_mode == "swahili":
            recommender_input = context_text + "\n\nPendekeza swali fupi la uchunguzi linalofuata. Format: Swahili: ..."
        else:
            recommender_input = context_text + "\n\nSuggest the next most relevant bilingual question only. Format as:\nEnglish: ...\n\nSwahili: ..."

        rec = run_task(agents["question_recommender_agent"], recommender_input, "Question Suggestion")

        if language_mode == "english":
            english_q, swahili_q = rec.strip(), ""
        elif language_mode == "swahili":
            english_q, swahili_q = "", rec.strip()
        else:
            match = re.search(r"English:\s*(.+?)\n+Swahili:\s*(.+)", rec, re.DOTALL)
            if match:
                english_q, swahili_q = match.group(1).strip(), match.group(2).strip()
            else:
                english_q, swahili_q = rec.strip(), ""

        yield sse_recommender(english_q, swahili_q, log_hook, session_id)

    return


# Mode 3: Live transcription (continuous patient finals → recommender only)
def live_transcription_stream(
    initial_message: str,
    language_mode: str = 'bilingual',
    speaker_role: str = 'patient',
    conversation_history: list | None = None,
    log_hook=None,
    session_id=None,
    # FIX #4: Accept live_state and lock so throttle uses real server-side timestamps.
    live_state: dict | None = None,
    live_state_lock=None,
):
    """
    Live transcription mode (final-driven) with coherence validation.
    - We receive FINAL text once per chunk and recommend next question.
    - Finalize path outputs Listener Summary + Final Plan (Listener-only for live mic mode).
    - Throttle now uses server-side timestamps stored in live_state["last_reco_ts"].
    """
    llm = load_llm()
    agents = load_agents_from_yaml(AGENT_PATH, llm)
    history = conversation_history or []

    # Finalize path (Listener-only in Live Mic mode)
    if speaker_role.lower() == "finalize":
        transcript_lines = [f"{m.get('role', '')}: {m.get('message', '')}" for m in history]
        convo_text = "\n".join(transcript_lines)

        bundle = build_listener_bundle(convo_text, language_mode=language_mode)
        yield sse_message("Listener", bundle, log_hook, session_id)
        return

    final_text = (initial_message or "").strip()
    if not final_text:
        return

    context_lines = [f"{m.get('role')}: {m.get('message')}" for m in history[-10:]]
    context_text = "\n".join(context_lines)

    if not is_coherent_medical_text(final_text, context_text):
        logger.info(f"Filtered incoherent transcription: {final_text}")
        return

    # 1) Emit patient final line
    yield sse_message("Patient", final_text, log_hook, session_id)

    # -------------------- FIX #4: CORRECTED THROTTLE --------------------
    # Check using live_state["last_reco_ts"] (server-side epoch timestamp).
    # The old check used conversation_history which never contains
    # question_recommender entries, so it was always False.
    if live_state is not None and live_state_lock is not None:
        if should_throttle_reco(live_state, live_state_lock):
            return
    # ---------------------------------------------------------------

    # 2) Build recommender context from full history
    context_lines = [f"{m.get('role')}: {m.get('message')}" for m in history]
    context_text = "\n".join(context_lines)

    if language_mode == "english":
        recommender_input = context_text + "\n\nSuggest the next most relevant diagnostic question. Format: English: ..."
    elif language_mode == "swahili":
        recommender_input = context_text + "\n\nPendekeza swali fupi la uchunguzi linalofuata. Format: Swahili: ..."
    else:
        recommender_input = context_text + "\n\nSuggest the next most relevant bilingual question only. Format as:\nEnglish: ...\n\nSwahili: ..."

    rec = run_task(agents["question_recommender_agent"], recommender_input, "Question Suggestion")

    if language_mode == "english":
        english_q, swahili_q = rec.strip(), ""
    elif language_mode == "swahili":
        english_q, swahili_q = "", rec.strip()
    else:
        match = re.search(r"English:\s*(.+?)\n+Swahili:\s*(.+)", rec, re.DOTALL)
        if match:
            english_q, swahili_q = match.group(1).strip(), match.group(2).strip()
        else:
            english_q, swahili_q = rec.strip(), ""

    yield sse_recommender(english_q, swahili_q, log_hook, session_id)

    # Update the server-side throttle timestamp AFTER emitting
    if live_state is not None and live_state_lock is not None:
        record_reco_emitted(live_state, live_state_lock)

    return


def simulate_agent_chat(user_message):
    llm = load_llm()
    agent_dict = load_agents_from_yaml(AGENT_PATH, llm)
    tasks = load_tasks_from_yaml(TASK_PATH, agent_dict)

    if not agent_dict or not tasks:
        return [{"role": "system", "message": "Error: No agents or tasks loaded."}]

    tasks[0].description += f"\n\nPatient says: {user_message}"

    crew = Crew(
        agents=list(agent_dict.values()),
        tasks=tasks,
        verbose=True
    )

    output = crew.kickoff()

    if isinstance(output, str):
        result_text = output
    elif hasattr(output, 'final_output'):
        result_text = output.final_output
    elif hasattr(output, 'result'):
        result_text = output.result
    else:
        result_text = str(output)

    transcript = [
        {"role": "Patient", "message": user_message, "timestamp": datetime.now().strftime("%H:%M:%S")},
        {"role": "Clinician", "message": result_text, "timestamp": datetime.now().strftime("%H:%M:%S")}
    ]
    return transcript
