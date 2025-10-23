from flask import Flask, render_template, request, jsonify, session, Response, stream_with_context
import os
import json
import logging
from medical_case_faiss import MedicalCaseFAISS
from config import Config
from crew_runner import simulate_agent_chat
from crew_runner import simulate_agent_chat_stepwise, real_actor_chat_stepwise, live_transcription_stream
from urllib.parse import unquote
import subprocess
import uuid
import tempfile
import whisper
import json
from datetime import datetime
from models import init_db, create_conversation, log_message, SessionLocal
from flask_login import current_user, login_required
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
import os, json, threading, queue, subprocess, numpy as np
import webrtcvad
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from flask_sock import Sock

load_dotenv()

# --- STT Config (env from your old app) ---
FFMPEG = os.getenv("FFMPEG", "ffmpeg")   # e.g. C:\ffmpeg\...\ffmpeg.exe
SAMPLE_RATE = 16000
MODEL_SIZE = os.getenv("MODEL_SIZE", "tiny")
DEVICE = os.getenv("DEVICE", "auto")     # auto | cpu | cuda



CONV_LOG_FILE = "conversation_logs.json"

# from crew_runner import simulate_agent_chat_stepwise
os.environ['CREWAI_TELEMETRY_DISABLED'] = '1'

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

#auth/login
from auth import auth_bp, login_manager
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf

app = Flask(__name__)
app.config.from_object(Config)

app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not app.debug

login_manager.init_app(app)
csrf = CSRFProtect(app)
app.register_blueprint(auth_bp)
from admin import admin_bp
app.register_blueprint(admin_bp)
sock = Sock(app)


CONV_LOG_FILE = "conversation_logs.json"
os.environ['CREWAI_TELEMETRY_DISABLED'] = '1'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Initialize FAISS system globally
faiss_system = None

def _pick_device():
    if DEVICE == "cuda":
        return ("cuda", "int8_float16")
    if DEVICE == "cpu":
        return ("cpu", "int8")
    # auto → pick cuda if CUDA_VISIBLE_DEVICES is set
    return ("cuda", "int8_float16") if os.getenv("CUDA_VISIBLE_DEVICES") else ("cpu", "int8")

_model_device, _compute_type = _pick_device()
model = WhisperModel(MODEL_SIZE, device=_model_device, compute_type=_compute_type)

@app.get('/csrf-token')
def get_csrf_token():
    """
    Frontend fetches this and stores the token in window.CSRF_TOKEN.
    All subsequent POSTs include X-CSRFToken header.
    """
    token = generate_csrf()
    return jsonify({'csrfToken': token})

def initialize_faiss():
    """Initialize the FAISS system on startup"""
    global faiss_system
    try:
        faiss_system = MedicalCaseFAISS()

        # Try to load existing index
        if (os.path.exists(app.config['FAISS_INDEX_PATH']) and
                os.path.exists(app.config['FAISS_METADATA_PATH'])):
            logger.info("Loading existing FAISS index...")
            faiss_system.load_index(
                app.config['FAISS_INDEX_PATH'],
                app.config['FAISS_METADATA_PATH']
            )
            logger.info("FAISS system loaded successfully!")
        else:
            logger.error("FAISS index files not found. Please build the database first.")
            return False

        return True
    except Exception as e:
        logger.error(f"Failed to initialize FAISS system: {e}")
        return False

# whisper model
whisper_model = None

def get_whisper_model():
    global whisper_model
    if whisper_model is None:
        whisper_model = whisper.load_model(os.getenv("WHISPER_MODEL", "tiny"))
    return whisper_model

# --- put near your imports/config ---
import shutil

# Pick an ffmpeg binary sensibly (env → common Windows path → PATH)
FFMPEG_BIN = os.getenv(
    "FFMPEG_BIN",
    r"C:\ffmpeg\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe" if os.name == "nt" else "ffmpeg"
)
if shutil.which(FFMPEG_BIN) is None:
    # Try generic name on PATH
    alt = shutil.which("ffmpeg")
    if alt:
        FFMPEG_BIN = alt

import subprocess, tempfile

def convert_to_wav_16k(src_path):
    dst_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.wav")
    cmd = ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "16000", "-f", "wav", dst_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return dst_path




def save_message_to_log(session_id, role, message, timestamp, type_="message"):
    """Append a single event to a JSON array log and to console."""
    entry = {
        "session_id": session_id,
        "role": role,
        "message": message,
        "timestamp": timestamp,
        "type": type_,
    }
    try:
        try:
            with open(CONV_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logs = []

        logs.append(entry)
        with open(CONV_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)

        logger.info(f"[{timestamp}] ({session_id}) {role} [{type_}]: {message}")
    except Exception as e:
        logger.error(f"Failed to save log: {e}")

@app.route('/agent_chat_stream')
@login_required
def agent_chat_stream():
    if not current_user.is_authenticated:
        return "Forbidden", 403

    message = request.args.get('message', '').strip()
    language = request.args.get('lang', 'bilingual').strip().lower()
    role = request.args.get('role', 'patient').strip().lower()
    mode = request.args.get('mode', 'real').strip().lower()

    if not message:
        return jsonify({'error': 'No message provided'}), 400

    # Ensure we have a conversation id; create and attach owner once (via column)
    if not session.get('id'):
        session['id'] = create_conversation(owner_user_id=current_user.id)
        session['conv'] = []
    sid = session['id']

    # Append this turn to the session transcript
    conv = session.get('conv', [])
    conv.append({"role": role, "message": message})
    session['conv'] = conv

    # Persist to DB whenever the generator yields messages
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
            session_id=sid
        )
    elif mode == "live":
        # In live mode, treat all chunks as patient unless role=finalize
        generator = live_transcription_stream(
            unquote(message),
            language_mode=language,
            speaker_role=role,
            conversation_history=conv,
            log_hook=log_hook,
            session_id=sid
        )
    else:
        # Real actors (turn-based)
        generator = real_actor_chat_stepwise(
            unquote(message),
            language_mode=language,
            speaker_role=role,
            conversation_history=conv,
            log_hook=log_hook,
            session_id=sid
        )

    return Response(stream_with_context(generator), mimetype='text/event-stream')



def has_role(self, name:str) -> bool:
    return any(r.name == name for r in self.roles)


@app.get("/csrf-token")
def get_csrf():
    return {"csrfToken": generate_csrf()}

@csrf.exempt
@app.route('/reset_conv', methods=['POST'])
@login_required
def reset_conv():
    
    session['conv'] = []
    cid = create_conversation(owner_user_id=current_user.id)
    session['id'] = cid

    return jsonify({'ok': True, 'conversation_id': cid})


@csrf.exempt
@app.route('/transcribe_audio', methods=['POST'])
def transcribe_audio():
    try:
        audio = request.files.get('audio')
        language = request.form.get("lang", "bilingual").lower()
        if not audio:
            return jsonify({'error': 'No audio uploaded'}), 400

        # save upload
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as temp_audio:
            audio.save(temp_audio.name)

        wav_path = convert_to_wav_16k(temp_audio.name)

        # load whisper model
        model = get_whisper_model()

        
        opts = dict(
            task="transcribe",
            temperature=0.0,
            beam_size=5,
            condition_on_previous_text=False,
            fp16=False,
            verbose=False,
            initial_prompt="Clinician and patient discussing symptoms such as chest pain, cough, wheezing, shortness of breath."
        )

    
        def two_pass_pick_en_sw(path):
            res_en = model.transcribe(path, language="en", **opts)
            res_sw = model.transcribe(path, language="sw", **opts)
            seg_en = (res_en.get('segments') or [{}])[0]
            seg_sw = (res_sw.get('segments') or [{}])[0]
            lp_en = seg_en.get('avg_logprob', -999)
            lp_sw = seg_sw.get('avg_logprob', -999)
            return res_en if lp_en >= lp_sw else res_sw

        if language == "english":
            result = model.transcribe(wav_path, language="en", **opts)
        elif language == "swahili":
            result = model.transcribe(wav_path, language="sw", **opts)
        else:  # 'bilingual'
            result = two_pass_pick_en_sw(wav_path)

        text = (result.get('text') or '').strip()
        return jsonify({'text': text})
    except Exception:
        logger.exception("Error during audio transcription")
        return jsonify({'error': 'Audio transcription failed'}), 500



@app.route('/')
def index():
    """Main search page"""
    return render_template('index.html')
@app.route('/agent_chat', methods=['POST'])
def agent_chat():
    try:
        data = request.get_json(force=True, silent=True)
        logger.info(f"Received JSON payload for /agent_chat: {data}")

        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid JSON. Expected object with "message" field.'}), 400

        user_message = data.get('message', '').strip()
        if not user_message:
            return jsonify({'error': 'Message cannot be empty'}), 400

        generator = simulate_agent_chat_stepwise(user_message)
        return Response(stream_with_context(generator), mimetype='text/event-stream')

    except Exception as e:
        logger.exception("Agent chat error")
        return jsonify({'error': 'An error occurred during agent chat'}), 500



@csrf.exempt
@app.route('/search', methods=['POST'])
def search():
    """Handle search requests"""
    try:
        data = request.get_json()
        query = data.get('query', '').strip()

        if not query:
            return jsonify({'error': 'Query cannot be empty'}), 400

        # Get search parameters
        k = min(data.get('max_results', 10), app.config['MAX_RESULTS'])
        similarity_threshold = data.get('similarity_threshold', app.config['DEFAULT_SIMILARITY_THRESHOLD'])

        # Search for similar cases
        results = faiss_system.search_similar_cases(
            query,
            k=k,
            similarity_threshold=similarity_threshold
        )

        # Get suggested questions
        suggested_questions = faiss_system.suggest_questions(
            query,
            k=k,
            max_questions=app.config['MAX_QUESTIONS'],
            similarity_threshold=similarity_threshold
        )

        # Format results for JSON response
        formatted_results = []
        for result in results:
            formatted_results.append({
                'case_id': result.case_id,
                'similarity_score': round(result.similarity_score, 4),
                'patient_background': result.patient_background,
                'chief_complaint': result.chief_complaint,
                'medical_history': result.medical_history,
                'opening_statement': result.opening_statement,
                'recommended_questions': result.recommended_questions[:5],  # Top 5 questions
                'red_flags': result.red_flags,
                'Suspected_illness':result.Suspected_illness
            })

        # Format suggested questions
        formatted_questions = []
        for q in suggested_questions:
            formatted_questions.append({
                'question': q['question'],
                'response': q.get('response', {}),
                'similarity_score': round(q['similarity_score'], 4),
                'case_id': q['case_id']
            })

        return jsonify({
            'query': query,
            'results': formatted_results,
            'suggested_questions': formatted_questions,
            'total_results': len(formatted_results)
        })

    except Exception as e:
        logger.error(f"Search error: {e}")
        return jsonify({'error': 'An error occurred during search'}), 500


@app.route('/case/<case_id>')
def get_case_details(case_id):
    """Get detailed information about a specific case"""
    try:
        case_details = faiss_system.get_case_details(case_id)

        if case_details:
            return jsonify(case_details)
        else:
            return jsonify({'error': 'Case not found'}), 404

    except Exception as e:
        logger.error(f"Error getting case details: {e}")
        return jsonify({'error': 'An error occurred'}), 500


@app.route('/demo')
def demo():
    """Get demo queries for testing"""
    demo_queries = [
        "finger pain stiffness morning",
        "breathing difficulty night cough",
        "joint pain swelling",
        "wheezing chest whistling sound",
        "fatigue hand pain work difficulty",
        "headache fever nausea",
        "chest pain shortness breath",
        "dizziness balance problems"
    ]

    return jsonify({'demo_queries': demo_queries})


@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'faiss_loaded': faiss_system is not None})


@app.errorhandler(404)
def not_found(error):
    return render_template('index.html'), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

#roles
current_speaker_role = "patient"  # default

@app.route("/set_role", methods=["POST"])
def set_role():
    global current_speaker_role
    role = request.json.get("role")
    if role in ["patient", "clinician"]:
        current_speaker_role = role
    return jsonify({"status": "ok", "role": current_speaker_role})


@app.route('/admin')
@login_required
def admin_page():
    if not any(r.name == "admin" for r in current_user.roles):
        return "Forbidden", 403
    return render_template('admin.html')


# --- NEW live chat implementation---
def start_ffmpeg_decoder():
    # stdin: webm/opus → stdout: raw 16k mono s16le PCM
    return subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error",
         "-i", "pipe:0",
         "-ar", str(SAMPLE_RATE), "-ac", "1",
         "-f", "s16le", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )

def pcm_s16le_bytes_to_float32(arr: bytes) -> np.ndarray:
    a = np.frombuffer(arr, dtype=np.int16).astype(np.float32)
    return a / 32768.0

def has_enough_signal(audio_f32: np.ndarray, min_secs=0.3, min_rms=0.002) -> bool:
    if audio_f32.size < int(SAMPLE_RATE * min_secs):
        return False
    rms = float(np.sqrt(np.mean(audio_f32**2))) if audio_f32.size else 0.0
    return rms >= min_rms


# --- NEW: WebSocket STT (live) ---
@sock.route("/ws/stt")
def stt(ws):
    # /ws/stt?lang=bilingual|en|sw (None == auto/mixed)
    qs = ws.environ.get("QUERY_STRING") or ""
    lang_raw = "bilingual"
    if "lang=" in qs:
        try:
            lang_raw = qs.split("lang=")[1].split("&")[0].lower()
        except Exception:
            pass

    if lang_raw in ("english", "en"):
        lang_arg = "en"
    elif lang_raw in ("swahili", "sw", "kiswahili", "ki_swahili"):
        lang_arg = "sw"
    elif lang_raw in ("bilingual", "auto", ""):
        lang_arg = None 
    else:
        lang_arg = None  # be safe and auto-detect

    ff = start_ffmpeg_decoder()
    pcm_q: "queue.Queue[bytes]" = queue.Queue()
    stop = threading.Event()
    vad = webrtcvad.Vad(2)  # 0..3 (2 is balanced)

    # Reader: ffmpeg stdout → PCM queue
    def read_pcm():
        try:
            chunk_bytes = 320 * 5  # ~100ms @16k mono s16
            while not stop.is_set():
                data = ff.stdout.read(chunk_bytes)
                if not data:
                    break
                pcm_q.put(data)
        finally:
            stop.set()

    # Writer: WS → ffmpeg stdin
    def write_webm():
        try:
            while not stop.is_set():
                msg = ws.receive()
                if msg is None:
                    break
                ff.stdin.write(msg)
                ff.stdin.flush()
        except Exception:
            pass
        finally:
            try:
                ff.stdin.close()
            except Exception:
                pass
            stop.set()

    threading.Thread(target=read_pcm, daemon=True).start()
    threading.Thread(target=write_webm, daemon=True).start()

    ring = bytearray()
    last_emit = 0
    silence_run = 0
    FRAME = 160 * 2 * 3  # 30ms frame bytes @16k s16 mono

    try:
        while not stop.is_set():
            try:
                block = pcm_q.get(timeout=0.5)
                ring += block
            except queue.Empty:
                continue

            # VAD on most recent 30ms
            if len(ring) >= FRAME:
                frame = bytes(ring[-FRAME:])
                voiced = vad.is_speech(frame, SAMPLE_RATE)
            else:
                voiced = False

            silence_run = 0 if voiced else (silence_run + 1)

            # PARTIAL ~ every 0.8s
            target_bytes_per_sec = SAMPLE_RATE * 2
            if len(ring) - last_emit >= int(target_bytes_per_sec * 0.8):
                audio = pcm_s16le_bytes_to_float32(ring)
                if has_enough_signal(audio, min_secs=0.35, min_rms=0.002):
                    try:
                        segments, _ = model.transcribe(
                            audio,
                            language=lang_arg,          # None => auto
                            vad_filter=False,           # avoid empty-after-filter
                            condition_on_previous_text=True,
                            beam_size=1,
                            without_timestamps=True,
                        )
                        text = "".join(s.text for s in segments).strip()
                        if text:
                            ws.send(json.dumps({"type": "partial", "text": text}))
                            last_emit = len(ring)
                    except ValueError:
                        pass  # autodetect can fail on near-silence

            # FINAL after ~600ms silence
            if silence_run >= 20:
                audio = pcm_s16le_bytes_to_float32(ring)
                if has_enough_signal(audio, min_secs=0.35, min_rms=0.002):
                    try:
                        segments, _ = model.transcribe(
                            audio,
                            language=lang_arg,
                            vad_filter=True,            # cleaner final
                            condition_on_previous_text=True,
                            beam_size=1,
                            word_timestamps=False,
                        )
                        text = "".join(s.text for s in segments).strip()
                        if text:
                            ws.send(json.dumps({"type": "final", "text": text}))
                            # (Optional) If you want to save finals in your DB here, do it now.
                    except ValueError:
                        pass

                # reset after endpoint
                ring.clear()
                last_emit = 0
                silence_run = 0

    finally:
        stop.set()
        try:
            ff.terminate()
        except Exception:
            pass

# --- Live sub-mode (remember asked / unasked questions) ---
import math, threading
import numpy as np
from flask import request, jsonify, session
from flask_login import login_required

# 1) Config
SIM_THRESHOLD = 0.74        # good starting point for embeddings
EMB_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
USE_EMBEDDINGS = True       # turn ON embeddings and disable the old heuristic

# 2) Session-safe plan (lists only) — keep your existing versions if already present
def _get_plan() -> dict:
    plan = session.get('qplan') or {'required': [], 'asked_ids': []}
    plan['required'] = list(plan.get('required') or [])
    plan['asked_ids'] = list(plan.get('asked_ids') or [])
    return plan

def _save_plan(plan: dict) -> None:
    session['qplan'] = {
        'required': list(plan.get('required') or []),
        'asked_ids': list(plan.get('asked_ids') or [])
    }
    session.modified = True

# 3) Embedding model (lazy load) + cache
_model_lock = threading.Lock()
_model = None

def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer(EMB_MODEL_NAME)
    return _model

# In-memory cache: { session_key: { qid: np.ndarray (d,) } }
_EMB_CACHE = {}
_CACHE_LOCK = threading.Lock()

def _session_key() -> str:
    # Use a stable per-conversation/session key you already have.
    # If you set session['id'] elsewhere, use that; otherwise fall back to Flask session SID-like key.
    return str(session.get('id') or id(session))

def _embed(texts: list[str]) -> np.ndarray:
    """Return (n, d) L2-normalized embeddings."""
    model = _get_model()
    vecs = model.encode(texts, normalize_embeddings=True)  # already L2-normalized
    if not isinstance(vecs, np.ndarray):
        vecs = np.asarray(vecs)
    return vecs

def _store_question_embeds(session_key: str, items: list[dict]):
    """Compute and cache embeddings for new questions only."""
    if not items:
        return
    with _CACHE_LOCK:
        bucket = _EMB_CACHE.setdefault(session_key, {})
        to_compute = [(q['id'], q['text']) for q in items if q['id'] not in bucket]
    if not to_compute:
        return
    qids, texts = zip(*to_compute)
    vecs = _embed(list(texts))
    with _CACHE_LOCK:
        bucket = _EMB_CACHE.setdefault(session_key, {})
        for i, qid in enumerate(qids):
            bucket[qid] = vecs[i]

def _get_embeds_for_unasked(session_key: str, unasked: list[dict]) -> tuple[list[str], np.ndarray]:
    """Ensure embeddings exist for unasked questions; return (ids, matrix (n,d))."""
    ids, missing = [], []
    with _CACHE_LOCK:
        bucket = _EMB_CACHE.setdefault(session_key, {})
        for q in unasked:
            qid = q['id']; ids.append(qid)
            if qid not in bucket:
                missing.append((qid, q['text']))
    if missing:
        qids, texts = zip(*missing)
        vecs = _embed(list(texts))
        with _CACHE_LOCK:
            bucket = _EMB_CACHE.setdefault(session_key, {})
            for i, qid in enumerate(qids):
                bucket[qid] = vecs[i]
    # Gather in the same order as ids
    with _CACHE_LOCK:
        mat = np.vstack([_EMB_CACHE[session_key][qid] for qid in ids]) if ids else np.zeros((0, 384), dtype=np.float32)
    return ids, mat

def _cosine_scores(vec: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """Cosine scores since embeddings are normalized; shape (n,)."""
    if mat.size == 0:
        return np.zeros((0,), dtype=np.float32)
    return mat @ vec  # (n,d) @ (d,) -> (n,)

# 4) Routes (keep your @login_required and optional @csrf.exempt)
#    A) Append questions and pre-embed
@csrf.exempt
@app.post('/live/plan')
@login_required
def live_plan_append():
    data = request.get_json(force=True, silent=True) or {}
    reqs = data.get('required') or []

    plan = _get_plan()
    have_ids = {q['id'] for q in plan['required'] if 'id' in q}

    merged, new_items = list(plan['required']), []
    for i, q in enumerate(reqs):
        if isinstance(q, dict):
            qid  = (q.get('id') or f"q_{i}").strip()
            qtxt = (q.get('text') or '').strip()
        else:
            qid, qtxt = f"q_{i}", str(q).strip()
        if not qtxt or qid in have_ids:
            continue
        merged.append({'id': qid, 'text': qtxt})
        new_items.append({'id': qid, 'text': qtxt})
        have_ids.add(qid)

    plan['required'] = merged
    _save_plan(plan)

    # Pre-compute and cache embeddings for these new questions
    if USE_EMBEDDINGS and new_items:
        _store_question_embeds(_session_key(), new_items)

    return jsonify({'ok': True, 'required_count': len(plan['required'])})

#    B) Mark asked by similarity (embeddings-first)
@csrf.exempt
@app.post('/live/mark_asked')
@login_required
def live_mark_by_similarity():
    data = request.get_json(force=True, silent=True) or {}
    mark_id = (data.get('id') or '').strip()
    text    = (data.get('text') or '').strip()

    plan = _get_plan()
    asked_set = set(plan['asked_ids'])
    matched = []

    if mark_id:
        # explicit tick
        for q in plan['required']:
            if q.get('id') == mark_id:
                asked_set.add(mark_id)
                matched.append(mark_id)
                break

    elif text:
        # embeddings path
        unasked = [q for q in plan['required'] if q.get('id') not in asked_set]
        if unasked:
            # ensure question embeddings
            q_ids, q_mat = _get_embeds_for_unasked(_session_key(), unasked)
            # compute utterance embedding once
            u_vec = _embed([text])[0]  # (d,)
            # cosine scores
            scores = _cosine_scores(u_vec, q_mat)  # (n,)
            for i, s in enumerate(scores):
                if float(s) >= SIM_THRESHOLD:
                    asked_set.add(q_ids[i])
                    matched.append(q_ids[i])

    plan['asked_ids'] = list(asked_set)
    _save_plan(plan)
    unasked_count = sum(1 for q in plan['required'] if q.get('id') not in asked_set)
    return jsonify({'ok': True, 'matched_ids': matched, 'asked_total': len(asked_set), 'unasked_count': unasked_count})

#    C) List unasked (unchanged)
@app.get('/live/unasked')
@login_required
def live_unasked():
    plan = _get_plan()
    asked_set = set(plan['asked_ids'])
    unasked = [q for q in plan['required'] if q.get('id') not in asked_set]
    return jsonify({
        'ok': True,
        'unasked': unasked,
        'unasked_count': len(unasked),
        'asked_count': len(asked_set),
        'required_count': len(plan['required'])
    })

# optional debug route you can add
@app.post('/live/_debug_scores')
@login_required
def live_debug_scores():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get('text') or '').strip()
    plan = _get_plan()
    sess = _session_key()
    # ensure embeds
    _store_question_embeds(sess, plan['required'])
    ids, mat = _get_embeds_for_unasked(sess, plan['required'])
    uvec = _embed([text])[0]
    scores = _cosine_scores(uvec, mat)
    rows = [{'id': ids[i], 'text': plan['required'][i]['text'], 'score': float(scores[i])} for i in range(len(ids))]
    rows.sort(key=lambda r: r['score'], reverse=True)
    return jsonify({'ok': True, 'threshold': SIM_THRESHOLD, 'top': rows[:5]})

@csrf.exempt
@app.post("/live/reset_plan")
@login_required
def live_reset_plan():
    session["qplan"] = {"required": [], "asked_ids": []}
    session.modified = True
    return jsonify({"ok": True})




if __name__ == '__main__':
    # Initialize FAISS system
    if initialize_faiss():
        init_db()
        logger.info("Starting Flask application...")
        app.run(debug=True, host='0.0.0.0', port=5000)
    else:
        logger.error("Failed to initialize FAISS system. Application cannot start.")
        logger.error("Please ensure the following files exist:")
        logger.error(f"- {app.config['FAISS_INDEX_PATH']}")
        logger.error(f"- {app.config['FAISS_METADATA_PATH']}")
        print("\nTo build the database, run your original script first:")
        print("python medical_case_faiss.py")