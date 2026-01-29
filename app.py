# app.py (Gemini-only STT) — UPDATED with Live "Unasked (end-only)" endpoints + per-session tracking

from flask import Flask, render_template, request, jsonify, session, Response, stream_with_context
import os
import json
import logging
from datetime import datetime
from urllib.parse import unquote
from threading import RLock

from dotenv import load_dotenv
from agent_loader import load_llm
from flask_login import current_user, login_required
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf

from config import Config
from medical_case_faiss import MedicalCaseFAISS
from crew_runner import (
    simulate_agent_chat_stepwise,
    real_actor_chat_stepwise,
    live_transcription_stream,
    rank_questions_for_unasked,  # ✅ NEW
    normalize_text,  # ✅ NEW
    build_listener_bundle,  # ✅ NEW (Listener summary + final plan for Live Stop)
)

from models import init_db, create_conversation, log_message
from flask_sock import Sock

# Auth/Admin
from auth import auth_bp, login_manager
from admin import admin_bp

# ✅ Gemini STT blueprint + WS routes
from stt_gemini import stt_bp, register_ws_routes

load_dotenv()

# -----------------------------------------------------------------------------
# App setup
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

os.environ["CREWAI_TELEMETRY_DISABLED"] = "1"

app = Flask(__name__)
app.config.from_object(Config)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not app.debug

login_manager.init_app(app)

csrf = CSRFProtect(app)
sock = Sock(app)

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

# -----------------------------------------------------------------------------
# ✅ Gemini STT registration (Gemini-only)
# -----------------------------------------------------------------------------
app.register_blueprint(stt_bp)  # POST /transcribe_audio
csrf.exempt(stt_bp)  # keep old behavior: allow JS uploads without CSRF header
register_ws_routes(sock)  # WS /ws/stt


# -----------------------------------------------------------------------------
# CSRF token endpoint (frontend can fetch this for other POST routes)
# -----------------------------------------------------------------------------
@app.get("/csrf-token")
def get_csrf_token():
    token = generate_csrf()
    return jsonify({"csrfToken": token})


# -----------------------------------------------------------------------------
# FAISS init
# -----------------------------------------------------------------------------
faiss_system = None


def initialize_faiss():
    """Initialize the FAISS system on startup."""
    global faiss_system
    try:
        faiss_system = MedicalCaseFAISS()

        if (os.path.exists(app.config["FAISS_INDEX_PATH"]) and
                os.path.exists(app.config["FAISS_METADATA_PATH"])):
            logger.info("Loading existing FAISS index...")
            faiss_system.load_index(
                app.config["FAISS_INDEX_PATH"],
                app.config["FAISS_METADATA_PATH"]
            )
            logger.info("FAISS system loaded successfully!")
        else:
            logger.error("FAISS index files not found. Please build the database first.")
            return False

        return True
    except Exception:
        logger.exception("Failed to initialize FAISS system")
        return False


# -----------------------------------------------------------------------------
# Live per-session plan store (in-memory)
# Keyed by (user_id, conversation_id)
# -----------------------------------------------------------------------------
LIVE_STATE_LOCK = RLock()
LIVE_STATE = {}  # (uid, cid) -> {"created_at": "...", "questions": {norm_q: {...}}, "asked": set(), "history": []}


def _ensure_conversation_id():
    """Ensure session['id'] exists and conv list exists."""
    if not session.get("id"):
        session["id"] = create_conversation(owner_user_id=current_user.id)
        session["conv"] = []
    if "conv" not in session:
        session["conv"] = []
    return session["id"]


def _live_key():
    cid = _ensure_conversation_id()

    # Flask-Login safe user id (string)
    uid = current_user.get_id()  # usually a string already
    if uid is None:
        uid = str(getattr(current_user, "id", "anon"))

    # conversation id might be UUID string or int; normalize to string
    return (str(uid), str(cid))


def _get_or_create_live_state():
    key = _live_key()
    with LIVE_STATE_LOCK:
        st = LIVE_STATE.get(key)
        if not st:
            st = {
                "created_at": datetime.utcnow().isoformat(),
                "questions": {},
                # norm_q -> {"question": original, "score": float|None, "added_at": "...", "asked": bool}
                "history": [],  # list of {"role":..., "message":...}
            }
            LIVE_STATE[key] = st
        return st


def _reset_live_state():
    key = _live_key()
    with LIVE_STATE_LOCK:
        LIVE_STATE.pop(key, None)


def _append_live_history(role: str, message: str):
    """Keep a lightweight history server-side too (separate from session['conv'])."""
    st = _get_or_create_live_state()
    msg = (message or "").strip()
    if not msg:
        return
    with LIVE_STATE_LOCK:
        st["history"].append({"role": role, "message": msg, "ts": datetime.utcnow().isoformat()})
        # Keep it from growing unbounded
        if len(st["history"]) > 400:
            st["history"] = st["history"][-300:]


# -----------------------------------------------------------------------------
# Agent chat streaming endpoint
# -----------------------------------------------------------------------------
@app.route("/agent_chat_stream")
@login_required
def agent_chat_stream():
    if not current_user.is_authenticated:
        return "Forbidden", 403

    message = request.args.get("message", "").strip()
    language = request.args.get("lang", "bilingual").strip().lower()
    role = request.args.get("role", "patient").strip().lower()
    mode = request.args.get("mode", "real").strip().lower()

    if not message:
        return jsonify({"error": "No message provided"}), 400

    # Ensure conversation id
    sid = _ensure_conversation_id()

    # Append turn to transcript (client session)
    conv = session.get("conv", [])
    conv.append({"role": role, "message": message})
    session["conv"] = conv

    # Also keep server-side history for live scoring (per session/user)
    try:
        _append_live_history(role, message)
    except Exception:
        logger.exception("Failed to append live history")

    # Persist hook to DB
    def log_hook(session_id, role_, message_, timestamp_, type_="message"):
        try:
            log_message(session_id, role_, message_, timestamp_, type_)
        except Exception:
            logger.exception("DB log failed")

    # Pick the streaming generator
    if mode == "simulated":
        generator = simulate_agent_chat_stepwise(
            unquote(message),
            language_mode=language,
            log_hook=log_hook,
            session_id=sid,
        )
    elif mode == "live":
        generator = live_transcription_stream(
            unquote(message),
            language_mode=language,
            speaker_role=role,
            conversation_history=conv,
            log_hook=log_hook,
            session_id=sid,
        )
    else:
        generator = real_actor_chat_stepwise(
            unquote(message),
            language_mode=language,
            speaker_role=role,
            conversation_history=conv,
            log_hook=log_hook,
            session_id=sid,
        )

    return Response(stream_with_context(generator), mimetype="text/event-stream")


# -----------------------------------------------------------------------------
# ✅ LIVE endpoints for "Unasked (end-only)"
# -----------------------------------------------------------------------------
@csrf.exempt
@app.route("/live/reset_plan", methods=["POST"])
@login_required
def live_reset_plan():
    _reset_live_state()
    return jsonify({"ok": True})


@csrf.exempt
@app.route("/live/plan", methods=["POST"])
@login_required
def live_plan():
    """
    Store recommended questions for the current live session.
    Expected payload (from app.js):
      { "required": [ {"id": "...", "text": "..."}, ... ] }
    """
    data = request.get_json(force=True, silent=True) or {}
    required = data.get("required") or []
    st = _get_or_create_live_state()

    now = datetime.utcnow().isoformat()
    added = 0

    with LIVE_STATE_LOCK:
        for item in required:
            q = (item.get("text") or "").strip()
            if not q:
                continue
            nq = normalize_text(q)
            if not nq:
                continue

            if nq not in st["questions"]:
                st["questions"][nq] = {
                    "question": q,
                    "score": None,  # filled when scoring at end
                    "added_at": now,
                    "asked": False,
                }
                added += 1

    return jsonify({"ok": True, "added": added, "total": len(st["questions"])})


@csrf.exempt
@app.route("/live/mark_asked", methods=["POST"])
@login_required
def live_mark_asked():
    """
    Mark recommended questions as asked based on a piece of FINAL transcript text.
    Payload: { "text": "..." }
    """
    data = request.get_json(force=True, silent=True) or {}
    final_text = (data.get("text") or "").strip()
    if not final_text:
        return jsonify({"ok": True, "matched": 0})

    st = _get_or_create_live_state()
    matched = 0

    norm_final = normalize_text(final_text)

    # lightweight matching:
    # - exact substring match on normalized
    # - token overlap threshold for short questions
    final_tokens = set(norm_final.split())

    with LIVE_STATE_LOCK:
        for nq, qobj in st["questions"].items():
            if qobj.get("asked"):
                continue

            # exact/substr match
            if nq and nq in norm_final:
                qobj["asked"] = True
                matched += 1
                continue

            # token overlap (helps when clinician paraphrases)
            q_tokens = set(nq.split())
            if not q_tokens:
                continue
            overlap = len(q_tokens & final_tokens)
            ratio = overlap / max(1, min(len(q_tokens), len(final_tokens)))

            # Threshold tuned to avoid false positives
            if overlap >= 3 and ratio >= 0.55:
                qobj["asked"] = True
                matched += 1

    return jsonify({"ok": True, "matched": matched})


@app.route("/live/unasked", methods=["GET"])
@login_required
def live_unasked():
    """
    Return unasked questions ranked in descending relevance.
    Response:
      { "unasked": [ {"question": "...", "score": 0.92}, ... ] }
    """
    language = (request.args.get("lang") or "bilingual").strip().lower()
    st = _get_or_create_live_state()

    with LIVE_STATE_LOCK:
        questions = [qobj["question"] for qobj in st["questions"].values() if not qobj.get("asked")]
        # Also build conversation text for ranking
        # Prefer server-side history; fallback to session conv
        history = st.get("history") or []
        if not history:
            history = session.get("conv", [])
        convo_text = "\n".join([f"{m.get('role', '')}: {m.get('message', '')}" for m in history])

    ranked = rank_questions_for_unasked(convo_text=convo_text, questions=questions, language_mode=language)

    # persist scores back into state (optional, so subsequent calls are stable)
    with LIVE_STATE_LOCK:
        for item in ranked:
            q = (item.get("question") or "").strip()
            nq = normalize_text(q)
            if nq in st["questions"]:
                st["questions"][nq]["score"] = item.get("score")

    # Ensure sorted desc
    ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

    return jsonify({"unasked": ranked})


@csrf.exempt
@app.route("/live/stop_bundle", methods=["POST"])
@login_required
def live_stop_bundle():
    """
    Called once when Live Mic STOP is clicked.
    Returns:
      - Listener Summary (EN+SW depending on language mode)
      - Listener FINAL PLAN (Listener-only; no clinician agent in live mode)
      - Unasked questions ranked by relevance

    Payload (optional): {"lang": "bilingual"|"english"|"swahili"}
    """
    data = request.get_json(force=True, silent=True) or {}
    language = (data.get("lang") or "bilingual").strip().lower()

    st = _get_or_create_live_state()

    with LIVE_STATE_LOCK:
        history = st.get("history") or []
        if not history:
            history = session.get("conv", [])
        convo_text = "\n".join([f"{m.get('role', '')}: {m.get('message', '')}" for m in history])

    # 1) Listener summary + final plan (single formatted block)
    try:
        listener_output = build_listener_bundle(convo_text, language_mode=language)
    except Exception:
        logger.exception("Failed to build listener bundle")
        listener_output = "Listener:\n**English Summary:**\n- —\n\n**Swahili Summary:**\n- —\n\n**FINAL PLAN:**\n- Step 1: —"

    # 2) Unasked questions (same logic as /live/unasked)
    with LIVE_STATE_LOCK:
        questions = [qobj["question"] for qobj in st["questions"].values() if not qobj.get("asked")]

    ranked = rank_questions_for_unasked(convo_text=convo_text, questions=questions, language_mode=language)

    with LIVE_STATE_LOCK:
        for item in ranked:
            q = (item.get("question") or "").strip()
            nq = normalize_text(q)
            if nq in st["questions"]:
                st["questions"][nq]["score"] = item.get("score")

    ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

    # Store post-stop context for optional follow-up chatbot
    try:
        with LIVE_STATE_LOCK:
            st["post_stop"] = {
                "convo_text": convo_text[-12000:],
                "listener_output": listener_output,
                "unasked": ranked,
                "lang": language,
                "saved_at": datetime.utcnow().isoformat(),
            }
            st.setdefault("followup", [])
            if len(st["followup"]) > 200:
                st["followup"] = st["followup"][-150:]
    except Exception:
        logger.exception("Failed to store post-stop context")

    return jsonify({
        "listener": {"message": listener_output, "timestamp": datetime.now().strftime("%H:%M:%S")},
        "unasked": ranked,
    })


# -----------------------------------------------------------------------------
# ✅ Optional clinician follow-up chatbot (Live only)
# -----------------------------------------------------------------------------
@csrf.exempt
@app.route("/live/followup_chat", methods=["POST"])
@login_required
def live_followup_chat():
    """Clinician can ask follow-up questions about the Listener FINAL PLAN / Unasked questions.

    The LLM is prompted with the context of the *most recently stopped* live mic session:
      - transcript text
      - listener summary + final plan
      - ranked unasked questions
      - previous follow-up Q/A turns

    Payload: {"message": "...", "lang": "bilingual"|"english"|"swahili"}
    Returns: {"answer": "..."}
    """
    data = request.get_json(force=True, silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "Message cannot be empty"}), 400

    lang = (data.get("lang") or "bilingual").strip().lower()

    st = _get_or_create_live_state()
    with LIVE_STATE_LOCK:
        post = st.get("post_stop") or {}
        convo_text = (post.get("convo_text") or "").strip()
        listener_output = (post.get("listener_output") or "").strip()
        unasked = post.get("unasked") or []
        follow_hist = st.get("followup") or []

    # If user asks before STOP / no context saved, fall back to session transcript
    if not convo_text:
        conv = session.get("conv", [])
        convo_text = "\n".join([f"{m.get('role', '')}: {m.get('message', '')}" for m in conv])

    # Build a compact context string
    unasked_lines = []
    for i, it in enumerate(unasked[:20], start=1):
        if isinstance(it, str):
            q = it.strip()
            sc = None
        else:
            q = str((it or {}).get("question") or "").strip()
            sc = (it or {}).get("score")
        if not q:
            continue
        if sc is None:
            unasked_lines.append(f"{i}. {q}")
        else:
            try:
                unasked_lines.append(f"{i}. {q} (score={float(sc):.3f})")
            except Exception:
                unasked_lines.append(f"{i}. {q}")

    followup_snips = []
    # Keep last ~8 turns for continuity
    for turn in follow_hist[-8:]:
        r = (turn.get("role") or "").strip().lower()
        m = (turn.get("message") or "").strip()
        if r and m:
            followup_snips.append(f"{r}: {m}")

    system_msg = (
        "You are a clinical reasoning assistant helping a clinician after a patient interview. "
        "You MUST use the provided session context (transcript, listener summary/final plan, and unasked questions). "
        "Answer the clinician's questions clearly and safely. "
        "If something is uncertain or not in the transcript, say so and suggest what to ask/verify. "
        "Do NOT invent patient facts. "
    )

    lang_note = ""
    if lang == "swahili":
        lang_note = "Respond in Swahili."
    elif lang == "english":
        lang_note = "Respond in English."
    else:
        lang_note = "Respond in English (you may add brief Swahili clarifications if helpful)."

    context_block = (
            f"\n\n=== SESSION TRANSCRIPT (most recent) ===\n{convo_text[-12000:]}\n"
            f"\n=== LISTENER SUMMARY + FINAL PLAN ===\n{listener_output}\n"
            f"\n=== UNASKED QUESTIONS (ranked) ===\n" + "\n".join(unasked_lines)
    )

    # Compose messages for ChatOpenAI
    messages = [
        {"role": "system", "content": system_msg + " " + lang_note + context_block},
    ]

    if followup_snips:
        messages.append({"role": "user", "content": "Follow-up chat so far:\n" + "\n".join(followup_snips)})

    messages.append({"role": "user", "content": user_msg})

    try:
        llm = load_llm()
        resp = llm.invoke(messages)
        answer = getattr(resp, "content", None) or str(resp)
    except Exception:
        logger.exception("Follow-up chat failed")
        return jsonify({"error": "Failed to generate follow-up response"}), 500

    # Save follow-up turn
    try:
        with LIVE_STATE_LOCK:
            st.setdefault("followup", []).append(
                {"role": "clinician", "message": user_msg, "ts": datetime.utcnow().isoformat()})
            st.setdefault("followup", []).append(
                {"role": "assistant", "message": answer, "ts": datetime.utcnow().isoformat()})
            if len(st["followup"]) > 200:
                st["followup"] = st["followup"][-150:]
    except Exception:
        logger.exception("Failed to store follow-up history")

    return jsonify({"answer": answer})


# -----------------------------------------------------------------------------
# Reset conversation
# -----------------------------------------------------------------------------
@csrf.exempt
@app.route("/reset_conv", methods=["POST"])
@login_required
def reset_conv():
    session["conv"] = []
    cid = create_conversation(owner_user_id=current_user.id)
    session["id"] = cid
    # also reset the live plan for this user/session
    try:
        _reset_live_state()
    except Exception:
        logger.exception("Failed to reset live state")
    return jsonify({"ok": True, "conversation_id": cid})


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# (Kept as-is; you can remove if unused)
@app.route("/agent_chat", methods=["POST"])
def agent_chat():
    try:
        data = request.get_json(force=True, silent=True)
        logger.info(f"Received JSON payload for /agent_chat: {data}")

        if not isinstance(data, dict):
            return jsonify({"error": 'Invalid JSON. Expected object with "message" field.'}), 400

        user_message = data.get("message", "").strip()
        if not user_message:
            return jsonify({"error": "Message cannot be empty"}), 400

        generator = simulate_agent_chat_stepwise(user_message)
        return Response(stream_with_context(generator), mimetype="text/event-stream")

    except Exception:
        logger.exception("Agent chat error")
        return jsonify({"error": "An error occurred during agent chat"}), 500


# -----------------------------------------------------------------------------
# FAISS search endpoints
# -----------------------------------------------------------------------------
@csrf.exempt
@app.route("/search", methods=["POST"])
def search():
    try:
        data = request.get_json() or {}
        query = (data.get("query") or "").strip()
        if not query:
            return jsonify({"error": "Query cannot be empty"}), 400

        k = min(int(data.get("max_results", 10)), app.config["MAX_RESULTS"])
        similarity_threshold = float(
            data.get("similarity_threshold", app.config["DEFAULT_SIMILARITY_THRESHOLD"])
        )

        results = faiss_system.search_similar_cases(
            query, k=k, similarity_threshold=similarity_threshold
        )

        suggested_questions = faiss_system.suggest_questions(
            query,
            k=k,
            max_questions=app.config["MAX_QUESTIONS"],
            similarity_threshold=similarity_threshold,
        )

        formatted_results = []
        for r in results:
            formatted_results.append(
                {
                    "case_id": r.case_id,
                    "similarity_score": round(r.similarity_score, 4),
                    "patient_background": r.patient_background,
                    "chief_complaint": r.chief_complaint,
                    "medical_history": r.medical_history,
                    "opening_statement": r.opening_statement,
                    "recommended_questions": r.recommended_questions[:5],
                    "red_flags": r.red_flags,
                    "Suspected_illness": r.Suspected_illness,
                }
            )

        formatted_questions = []
        for q in suggested_questions:
            formatted_questions.append(
                {
                    "question": q["question"],
                    "response": q.get("response", {}),
                    "similarity_score": round(q["similarity_score"], 4),
                    "case_id": q["case_id"],
                }
            )

        return jsonify(
            {
                "query": query,
                "results": formatted_results,
                "suggested_questions": formatted_questions,
                "total_results": len(formatted_results),
            }
        )

    except Exception:
        logger.exception("Search error")
        return jsonify({"error": "An error occurred during search"}), 500


@app.route("/case/<case_id>")
def get_case_details(case_id):
    try:
        case_details = faiss_system.get_case_details(case_id)
        if case_details:
            return jsonify(case_details)
        return jsonify({"error": "Case not found"}), 404
    except Exception:
        logger.exception("Error getting case details")
        return jsonify({"error": "An error occurred"}), 500


@app.route("/demo")
def demo():
    return jsonify(
        {
            "demo_queries": [
                "finger pain stiffness morning",
                "breathing difficulty night cough",
                "joint pain swelling",
                "wheezing chest whistling sound",
                "fatigue hand pain work difficulty",
                "headache fever nausea",
                "chest pain shortness breath",
                "dizziness balance problems",
            ]
        }
    )


@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "faiss_loaded": faiss_system is not None})


@app.errorhandler(404)
def not_found(error):
    return render_template("index.html"), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500


# -----------------------------------------------------------------------------
# Roles / admin
# -----------------------------------------------------------------------------
current_speaker_role = "patient"


@app.route("/set_role", methods=["POST"])
def set_role():
    global current_speaker_role
    role = (request.json or {}).get("role")
    if role in ["patient", "clinician"]:
        current_speaker_role = role
    return jsonify({"status": "ok", "role": current_speaker_role})


@app.route("/admin")
@login_required
def admin_page():
    if not any(r.name == "admin" for r in current_user.roles):
        return "Forbidden", 403
    return render_template("admin.html")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if initialize_faiss():
        init_db()
        logger.info("Starting Flask application (Gemini-only STT enabled)...")
        app.run(debug=True, host="0.0.0.0", port=5000)
    else:
        logger.error("Failed to initialize FAISS system. Application cannot start.")
        logger.error("Please ensure the following files exist:")
        logger.error(f"- {app.config['FAISS_INDEX_PATH']}")
        logger.error(f"- {app.config['FAISS_METADATA_PATH']}")
        print("\nTo build the database, run your original script first:")
        print("python medical_case_faiss.py")
