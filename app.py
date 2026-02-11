# app.py (Gemini-only STT) — UPDATED with Live "Unasked (end-only)" endpoints + per-session tracking

from flask import Flask, render_template, request, jsonify, session, Response, stream_with_context, redirect, url_for
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
    rank_questions_for_unasked,
    normalize_text,
    build_listener_bundle,
)

from models import (
    init_db,
    create_conversation,
    log_message,
    list_conversations_for_user,
    get_conversation_if_owned_by,
    get_conversation_messages,
    delete_conversation_if_owned_by,
    list_patients_for_user,
    create_patient,
    get_patient,
    get_next_global_patient_identifier,
)
from flask_sock import Sock

# Auth/Admin
from auth import auth_bp, login_manager
from admin import admin_bp, delete_conversation as admin_delete_conversation

# Gemini STT blueprint + WS routes
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
csrf.exempt(admin_delete_conversation)

# -----------------------------------------------------------------------------
# Gemini STT registration (Gemini-only)
# -----------------------------------------------------------------------------
app.register_blueprint(stt_bp)  # POST /transcribe_audio
csrf.exempt(stt_bp)             # allow JS uploads without CSRF header
register_ws_routes(sock)        # WS /ws/stt


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
LIVE_STATE = {}  # (uid, cid) -> {
#   "created_at": "...",
#   "questions": {norm_q: {...}},
#   "history": [],
#   "last_reco_ts": 0.0   # FIX #4: server-side throttle timestamp
# }


def _ensure_conversation_id():
    """Ensure session['id'] exists, belongs to current user, and conv list exists."""
    existing_cid = session.get("id")
    if existing_cid:
        c = get_conversation_if_owned_by(existing_cid, current_user.id)
        if c is None:
            # Stale or wrong-owner session: start fresh
            session.pop("id", None)
            session.pop("conv", None)
            session.pop("patient_id", None)
            existing_cid = None
    if not existing_cid:
        patient_id = session.get("patient_id")
        session["id"] = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
        session["conv"] = []
    if "conv" not in session:
        session["conv"] = []
    return session["id"]


# FIX #6: _live_key() no longer calls _ensure_conversation_id() (which did a
# DB write). It now reads the existing session cid without side effects.
# Callers that genuinely need a conversation id should call
# _ensure_conversation_id() explicitly first.
def _live_key():
    cid = session.get("id")
    if not cid:
        return None  # caller must handle gracefully

    uid = current_user.get_id()
    if uid is None:
        uid = str(getattr(current_user, "id", "anon"))

    return (str(uid), str(cid))


def _get_or_create_live_state():
    key = _live_key()
    if key is None:
        # No conversation in session yet — return a throwaway dict rather than
        # silently triggering a DB write.
        return {
            "created_at": datetime.utcnow().isoformat(),
            "questions": {},
            "history": [],
            "last_reco_ts": 0.0,
        }
    with LIVE_STATE_LOCK:
        st = LIVE_STATE.get(key)
        if not st:
            st = {
                "created_at": datetime.utcnow().isoformat(),
                "questions": {},
                "history": [],
                # FIX #4: initialise throttle timestamp
                "last_reco_ts": 0.0,
            }
            LIVE_STATE[key] = st
        return st


def _reset_live_state():
    key = _live_key()
    if key is None:
        return
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

    # Ensure conversation id (DB write only happens here, not in _live_key)
    sid = _ensure_conversation_id()

    conv = session.get("conv", [])
    conv.append({"role": role, "message": message})
    session["conv"] = conv

    try:
        _append_live_history(role, message)
    except Exception:
        logger.exception("Failed to append live history")

    def log_hook(session_id, role_, message_, timestamp_, type_="message"):
        try:
            log_message(session_id, role_, message_, timestamp_, type_)
        except Exception:
            logger.exception("DB log failed")

    # FIX #4: Pass live_state and lock to live_transcription_stream so the
    # throttle uses real server-side timestamps instead of broken client history.
    live_st = _get_or_create_live_state()

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
            live_state=live_st,
            live_state_lock=LIVE_STATE_LOCK,
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
# LIVE endpoints for "Unasked (end-only)"
# FIX #5: @csrf.exempt must be applied AFTER @app.route (closer to the function)
# so Flask-WTF marks the registered view function, not the raw callable.
# -----------------------------------------------------------------------------
@app.route("/live/reset_plan", methods=["POST"])
@login_required
@csrf.exempt
def live_reset_plan():
    _reset_live_state()
    return jsonify({"ok": True})


@app.route("/live/plan", methods=["POST"])
@login_required
@csrf.exempt
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
                    "score": None,
                    "added_at": now,
                    "asked": False,
                }
                added += 1

    return jsonify({"ok": True, "added": added, "total": len(st["questions"])})


@app.route("/live/mark_asked", methods=["POST"])
@login_required
@csrf.exempt
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
    final_tokens = set(norm_final.split())

    with LIVE_STATE_LOCK:
        for nq, qobj in st["questions"].items():
            if qobj.get("asked"):
                continue

            if nq and nq in norm_final:
                qobj["asked"] = True
                matched += 1
                continue

            q_tokens = set(nq.split())
            if not q_tokens:
                continue
            overlap = len(q_tokens & final_tokens)
            ratio = overlap / max(1, min(len(q_tokens), len(final_tokens)))

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
        history = st.get("history") or []
        if not history:
            history = session.get("conv", [])
        convo_text = "\n".join([f"{m.get('role', '')}: {m.get('message', '')}" for m in history])

    ranked = rank_questions_for_unasked(convo_text=convo_text, questions=questions, language_mode=language)

    with LIVE_STATE_LOCK:
        for item in ranked:
            q = (item.get("question") or "").strip()
            nq = normalize_text(q)
            if nq in st["questions"]:
                st["questions"][nq]["score"] = item.get("score")

    ranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

    return jsonify({"unasked": ranked})


@app.route("/live/stop_bundle", methods=["POST"])
@login_required
@csrf.exempt
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

    try:
        listener_output = build_listener_bundle(convo_text, language_mode=language)
    except Exception:
        logger.exception("Failed to build listener bundle")
        listener_output = "Listener:\n**English Summary:**\n- —\n\n**Swahili Summary:**\n- —\n\n**FINAL PLAN:**\n- Step 1: —"

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
# Optional clinician follow-up chatbot (Live only)
# -----------------------------------------------------------------------------
@app.route("/live/followup_chat", methods=["POST"])
@login_required
@csrf.exempt
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

    if not convo_text:
        conv = session.get("conv", [])
        convo_text = "\n".join([f"{m.get('role', '')}: {m.get('message', '')}" for m in conv])

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
# Reset conversation (optionally with patient_id)
# -----------------------------------------------------------------------------
@app.route("/reset_conv", methods=["POST"])
@login_required
@csrf.exempt
def reset_conv():
    data = request.get_json(force=True, silent=True) or {}
    patient_id = data.get("patient_id")
    if patient_id is not None:
        patient_id = int(patient_id)
    session["conv"] = []
    try:
        _reset_live_state()
    except Exception:
        logger.exception("Failed to reset live state")
    cid = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
    session["id"] = cid
    if patient_id is not None:
        session["patient_id"] = patient_id
    else:
        session.pop("patient_id", None)
    return jsonify({"ok": True, "conversation_id": cid})


# -----------------------------------------------------------------------------
# History & My conversations API
# -----------------------------------------------------------------------------
def _patients_display_order():
    """Single source of truth: patients for current user sorted by id."""
    patients = list_patients_for_user(current_user.id)
    return sorted(patients, key=lambda p: p.id)


def _patient_labels_for_current_user():
    """Stable mapping patient_id -> 'Patient 1', 'Patient 2', ..."""
    ordered = _patients_display_order()
    return {p.id: f"Patient {i + 1}" for i, p in enumerate(ordered)}


@app.route("/api/my-conversations")
@login_required
def api_my_conversations():
    """List current user's conversations. Excludes empty conversations."""
    convos = list_conversations_for_user(current_user.id)
    plabels = _patient_labels_for_current_user()
    out = []
    for c in convos:
        msgs = get_conversation_messages(c.id)
        if not msgs:
            continue
        first_msg = msgs[0].message if msgs and msgs[0].message else ""
        preview = (first_msg[:80] + "…") if len(first_msg) > 80 else first_msg
        pid = c.patient_id if c.patient_id is not None else (c.patient.id if getattr(c, "patient", None) else None)
        patient_label = plabels.get(int(pid)) if pid is not None else None
        if pid is not None and not patient_label:
            patient_label = "Patient"
        out.append({
            "id": c.id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "patient_id": pid,
            "patient_label": patient_label,
            "message_count": len(msgs),
            "preview": preview,
        })
    return jsonify({"ok": True, "conversations": out})


@app.route("/api/conversations/<conversation_id>/messages")
@login_required
def api_conversation_messages(conversation_id):
    """Messages for this conversation; 403 if not owned by current user."""
    c = get_conversation_if_owned_by(conversation_id, current_user.id)
    if c is None:
        return jsonify({"ok": False, "error": "Not found or access denied"}), 403
    msgs = get_conversation_messages(conversation_id)
    plabels = _patient_labels_for_current_user()
    out = [
        {
            "id": m.id,
            "role": m.role,
            "type": m.type or "message",
            "message": m.message,
            "timestamp": m.timestamp,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in msgs
    ]
    pid = c.patient_id if c.patient_id is not None else (c.patient.id if getattr(c, "patient", None) else None)
    patient_label = plabels.get(int(pid)) if pid is not None else None
    if pid is not None and not patient_label:
        patient_label = "Patient"
    return jsonify({
        "ok": True,
        "conversation_id": conversation_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "patient_id": pid,
        "patient_label": patient_label,
        "messages": out,
    })


@app.route("/api/conversations/<conversation_id>", methods=["DELETE"])
@login_required
@csrf.exempt
def api_delete_conversation(conversation_id):
    """Delete this conversation if owned by current user."""
    deleted = delete_conversation_if_owned_by(conversation_id, current_user.id)
    if not deleted:
        return jsonify({"ok": False, "error": "Not found or access denied"}), 404
    return jsonify({"ok": True})


# -----------------------------------------------------------------------------
# Session patient
# -----------------------------------------------------------------------------
@app.route("/api/session-patient", methods=["POST"])
@login_required
def api_session_patient():
    """Set session['patient_id'] so the next conversation is linked to this patient."""
    data = request.get_json(force=True, silent=True) or {}
    pid = data.get("patient_id")
    if pid is None:
        session.pop("patient_id", None)
    else:
        try:
            session["patient_id"] = int(pid)
        except (TypeError, ValueError):
            session.pop("patient_id", None)
    return jsonify({"ok": True})


# -----------------------------------------------------------------------------
# New conversation (GET)
# -----------------------------------------------------------------------------
@app.route("/new_conversation")
@login_required
def new_conversation():
    """Create a fresh conversation and redirect to index."""
    patient_id = request.args.get("patient_id")
    if patient_id is not None and str(patient_id).strip() != "":
        try:
            patient_id = int(patient_id)
        except (TypeError, ValueError):
            patient_id = None
    else:
        patient_id = None
    session["conv"] = []
    try:
        _reset_live_state()
    except Exception:
        logger.exception("Failed to reset live state")
    cid = create_conversation(owner_user_id=current_user.id, patient_id=patient_id)
    session["id"] = cid
    if patient_id is not None:
        session["patient_id"] = patient_id
    else:
        session.pop("patient_id", None)
    return redirect(url_for("index"))


# -----------------------------------------------------------------------------
# Patients API
# -----------------------------------------------------------------------------
@app.route("/api/patients", methods=["GET", "POST"])
@login_required
def api_patients():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        identifier = (data.get("identifier") or data.get("display_name") or "").strip()
        if not identifier:
            identifier = get_next_global_patient_identifier()
        display_name = (data.get("display_name") or "").strip() or None
        pid = create_patient(identifier=identifier, clinician_id=current_user.id, display_name=display_name)
        return jsonify({"ok": True, "patient_id": pid})
    ordered = _patients_display_order()
    out = [
        {"id": p.id, "label": f"Patient {i+1}", "created_at": p.created_at.isoformat() if p.created_at else None}
        for i, p in enumerate(ordered)
    ]
    return jsonify({"ok": True, "patients": out})


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/history")
@login_required
def history_page():
    return render_template("history.html")


@app.route("/history/<conversation_id>")
@login_required
def history_detail_page(conversation_id):
    c = get_conversation_if_owned_by(conversation_id, current_user.id)
    if c is None:
        return "Not found or access denied", 404
    msgs = get_conversation_messages(conversation_id)
    plabels = _patient_labels_for_current_user()
    pid = c.patient_id if c.patient_id is not None else (c.patient.id if getattr(c, "patient", None) else None)
    patient_label = plabels.get(int(pid)) if pid is not None else None
    if pid is not None and not patient_label:
        patient_label = "Patient"
    return render_template(
        "history_detail.html",
        conversation_id=conversation_id,
        created_at=c.created_at,
        patient_label=patient_label,
        patient_id=pid,
        messages=msgs,
    )


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
@app.route("/search", methods=["POST"])
@csrf.exempt
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
