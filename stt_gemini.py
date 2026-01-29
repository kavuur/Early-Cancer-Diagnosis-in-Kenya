# stt_gemini.py (Gemini STT engine: batch + websocket)
# Improvements:
# 1) Emits STT status events to the browser so you can show an "STT degraded" banner.
# 2) Optional local faster-whisper fallback when Gemini is overloaded.

import os
import io
import json
import time
import uuid
import queue
import shutil
import tempfile
import threading
import subprocess
import logging
import wave
import random
from typing import Optional, Callable, Any

import numpy as np
import webrtcvad
from flask import Blueprint, request, jsonify

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

logger = logging.getLogger(__name__)

stt_bp = Blueprint("stt_gemini", __name__)

# -----------------------------------------------------------------------------
# Config (env-driven)
# -----------------------------------------------------------------------------
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))

FFMPEG_BIN = os.getenv(
    "FFMPEG_BIN",
    r"C:\\ffmpeg\\ffmpeg-7.1.1-full_build\\bin\\ffmpeg.exe" if os.name == "nt" else "ffmpeg"
)
if shutil.which(FFMPEG_BIN) is None:
    alt = shutil.which("ffmpeg")
    if alt:
        FFMPEG_BIN = alt

# VAD / segmentation
STT_VAD_AGGRESSIVENESS = int(os.getenv("STT_VAD_AGGRESSIVENESS", "3"))   # 0..3
VAD_FRAME_MS = int(os.getenv("VAD_FRAME_MS", "30"))                     # 10/20/30
VAD_VOICED_RATIO_MIN = float(os.getenv("VAD_VOICED_RATIO_MIN", "0.65")) # 0..1

STT_SEGMENT_SILENCE_MS = int(os.getenv("STT_SEGMENT_SILENCE_MS", "1200"))
STT_MAX_SEGMENT_MS = int(os.getenv("STT_MAX_SEGMENT_MS", "10000"))

EMIT_PARTIALS = os.getenv("EMIT_PARTIALS", "false").lower() in ("1", "true", "yes", "y")
STT_PARTIAL_MIN_INTERVAL_MS = int(os.getenv("STT_PARTIAL_MIN_INTERVAL_MS", "700"))

# Retry behavior (overload handling)
GEMINI_MAX_ATTEMPTS = int(os.getenv("GEMINI_MAX_ATTEMPTS", "6"))
GEMINI_RETRY_BASE_DELAY = float(os.getenv("GEMINI_RETRY_BASE_DELAY", "0.6"))
GEMINI_RETRY_MAX_DELAY = float(os.getenv("GEMINI_RETRY_MAX_DELAY", "8.0"))

# Optional local fallback (faster-whisper)
USE_LOCAL_WHISPER_FALLBACK = os.getenv("USE_LOCAL_WHISPER_FALLBACK", "true").lower() in ("1", "true", "yes", "y")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")  # int8 / float16 etc.

# -----------------------------------------------------------------------------
# Optional faster-whisper
# -----------------------------------------------------------------------------
try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception:  # pragma: no cover
    WhisperModel = None

_WHISPER_MODEL: Optional[Any] = None


def _get_whisper_model() -> Optional[Any]:
    """Lazy-load faster-whisper model (if installed)."""
    global _WHISPER_MODEL
    if not USE_LOCAL_WHISPER_FALLBACK:
        return None
    if WhisperModel is None:
        return None
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL

    # If CUDA is available, faster-whisper can use it automatically when device='cuda'.
    # We default to 'auto' to avoid hard failures.
    device = os.getenv("WHISPER_DEVICE", "auto")
    try:
        _WHISPER_MODEL = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=WHISPER_COMPUTE_TYPE)
        logger.info(f"Loaded faster-whisper model: size={WHISPER_MODEL_SIZE}, device={device}, compute={WHISPER_COMPUTE_TYPE}")
    except Exception:
        logger.exception("Failed to load faster-whisper model; local fallback disabled")
        _WHISPER_MODEL = None
    return _WHISPER_MODEL


def _whisper_lang_code(lang: str) -> Optional[str]:
    lang = (lang or "bilingual").lower()
    if lang in ("english", "en"):
        return "en"
    if lang in ("swahili", "sw", "kiswahili"):
        return "sw"
    return None  # auto


def whisper_transcribe_pcm16(pcm_s16le: bytes, lang: str) -> str:
    """Transcribe a PCM16 mono segment using faster-whisper (local fallback)."""
    model = _get_whisper_model()
    if model is None:
        return ""

    wav_bytes = write_wav_bytes(pcm_s16le, sample_rate=SAMPLE_RATE)

    # Write to temp wav for faster-whisper (simple + reliable)
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(wav_bytes)
        tmp.flush()
        tmp.close()

        language = _whisper_lang_code(lang)
        segments, _info = model.transcribe(
            tmp.name,
            language=language,
            beam_size=int(os.getenv("WHISPER_BEAM_SIZE", "3")),
            vad_filter=False,
        )
        text_parts = []
        for seg in segments:
            t = (getattr(seg, "text", "") or "").strip()
            if t:
                text_parts.append(t)
        return " ".join(text_parts).strip()
    except Exception:
        logger.exception("Local faster-whisper fallback transcription failed")
        return ""
    finally:
        try:
            if tmp is not None and os.path.exists(tmp.name):
                os.unlink(tmp.name)
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Gemini client
# -----------------------------------------------------------------------------

def _gemini_client() -> genai.Client:
    """Prefer GEMINI_API_KEY if present. Fall back to GOOGLE_API_KEY only if needed."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    google_key = os.getenv("GOOGLE_API_KEY")

    key = gemini_key or google_key
    if not key:
        raise RuntimeError("Missing GEMINI_API_KEY (or GOOGLE_API_KEY) in environment.")

    return genai.Client(api_key=key)


def _gemini_model_name() -> str:
    return os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash")


def _gemini_model_candidates() -> list[str]:
    """Primary model + optional fallbacks (comma-separated env var)."""
    primary = (_gemini_model_name() or "").strip()
    fallbacks = (os.getenv("GEMINI_MODEL_FALLBACKS", "") or "").strip()

    models = [primary] if primary else []
    if fallbacks:
        models += [m.strip() for m in fallbacks.split(",") if m.strip()]

    # de-dup preserve order
    seen = set()
    out = []
    for m in models:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out or ["gemini-2.5-flash"]


def _is_overload_error(e: Exception) -> bool:
    # 503 overload is the important one here
    if isinstance(e, genai_errors.ServerError):
        return getattr(e, "status_code", None) == 503
    return False


def gemini_generate_with_retry(
    client: genai.Client,
    contents,
    config,
    max_attempts: int = GEMINI_MAX_ATTEMPTS,
    base_delay: float = GEMINI_RETRY_BASE_DELAY,
    max_delay: float = GEMINI_RETRY_MAX_DELAY,
    on_retry: Optional[Callable[[str, int, int, float], None]] = None,
    on_model_switch: Optional[Callable[[str], None]] = None,
):
    """Retry on 503 overload with exponential backoff + jitter; try fallback models."""
    last_err: Optional[Exception] = None
    models = _gemini_model_candidates()

    for model_i, model in enumerate(models):
        if model_i > 0 and on_model_switch:
            on_model_switch(model)

        delay = base_delay
        for attempt in range(1, max_attempts + 1):
            try:
                return client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                last_err = e

                if _is_overload_error(e):
                    # backoff + jitter, then retry same model
                    sleep_s = min(max_delay, delay) * (0.8 + random.random() * 0.4)
                    logger.warning(
                        f"Gemini overloaded (503) on model={model}. "
                        f"Retry {attempt}/{max_attempts} after {sleep_s:.2f}s"
                    )
                    if on_retry:
                        try:
                            on_retry(model, attempt, max_attempts, sleep_s)
                        except Exception:
                            pass
                    time.sleep(sleep_s)
                    delay *= 2
                    continue

                # Non-overload errors: don't spam retries
                raise

        logger.warning(f"Exhausted retries for model={model}; trying fallback if available...")

    if last_err:
        raise last_err
    raise RuntimeError("Gemini generate_content failed with unknown error")


def _effective_engine_name() -> str:
    # Keep primary name for UI consistency
    return _gemini_model_name()


# -----------------------------------------------------------------------------
# Audio utilities
# -----------------------------------------------------------------------------

def convert_to_wav_16k(src_path: str) -> str:
    """Convert any input audio to 16kHz mono WAV."""
    dst_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.wav")
    cmd = [
        FFMPEG_BIN,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        src_path,
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "wav",
        dst_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return dst_path


def pcm_s16le_bytes_to_float32(pcm: bytes) -> np.ndarray:
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    return a / 32768.0


def write_wav_bytes(pcm_s16le: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Return WAV bytes for PCM16 mono."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_s16le)
    return buf.getvalue()


def rms_level_f32(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio**2)))


# -----------------------------------------------------------------------------
# Gemini transcription (batch)
# -----------------------------------------------------------------------------

def _lang_prompt(lang: str) -> str:
    lang = (lang or "bilingual").lower()
    if lang in ("english", "en"):
        return "Transcribe the audio in English."
    if lang in ("swahili", "sw", "kiswahili"):
        return "Transcribe the audio in Swahili."
    return "Transcribe the audio. The speaker may use English and/or Swahili."


def gemini_transcribe_wav_file(wav_path: str, lang: str = "bilingual") -> str:
    """Batch transcription for a WAV file."""
    client = _gemini_client()
    prompt = _lang_prompt(lang)

    with open(wav_path, "rb") as f:
        wav_bytes = f.read()

    audio_part = genai_types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")

    contents = [
        genai_types.Content(
            role="user",
            parts=[
                genai_types.Part.from_text(text=prompt),
                audio_part,
            ],
        )
    ]

    resp = gemini_generate_with_retry(
        client=client,
        contents=contents,
        config=genai_types.GenerateContentConfig(temperature=0.0),
    )

    return (resp.text or "").strip()


# -----------------------------------------------------------------------------
# REST endpoint: POST /transcribe_audio
# -----------------------------------------------------------------------------

@stt_bp.post("/transcribe_audio")
def transcribe_audio():
    """Accept multipart form: audio file + lang; returns {text, engine}."""
    try:
        audio = request.files.get("audio")
        language = (request.form.get("lang") or "bilingual").lower()

        if not audio:
            return jsonify({"error": "No audio uploaded"}), 400

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            audio.save(tmp.name)
            src_path = tmp.name

        wav_path = convert_to_wav_16k(src_path)

        text = gemini_transcribe_wav_file(wav_path, lang=language)
        return jsonify({"text": text, "engine": _effective_engine_name()})

    except Exception:
        logger.exception("Gemini audio transcription failed")
        return jsonify({"error": "Audio transcription failed"}), 500


# -----------------------------------------------------------------------------
# WebSocket STT (live): WS /ws/stt
# -----------------------------------------------------------------------------

def start_ffmpeg_decoder():
    return subprocess.Popen(
        [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        bufsize=0,
    )


def parse_lang_query(qs: str) -> str:
    qs = qs or ""
    lang_raw = "bilingual"
    if "lang=" in qs:
        try:
            lang_raw = qs.split("lang=")[1].split("&")[0].lower()
        except Exception:
            lang_raw = "bilingual"

    if lang_raw in ("english", "en"):
        return "english"
    if lang_raw in ("swahili", "sw", "kiswahili"):
        return "swahili"
    return "bilingual"


def vad_voiced_ratio(pcm_s16le: bytes, sample_rate: int, vad: webrtcvad.Vad, frame_ms: int) -> float:
    frame_bytes = int(sample_rate * (frame_ms / 1000.0) * 2)
    if frame_bytes <= 0:
        return 0.0
    n = len(pcm_s16le) // frame_bytes
    if n <= 0:
        return 0.0
    voiced = 0
    for i in range(n):
        frame = pcm_s16le[i * frame_bytes : (i + 1) * frame_bytes]
        if len(frame) == frame_bytes and vad.is_speech(frame, sample_rate):
            voiced += 1
    return voiced / float(n)


class GeminiWorker:
    """Background worker that batches PCM segments and returns transcript text.

    Emits events:
      - {"type": "final", "text": "...", "engine": "..."}
      - {"type": "status", "level": "warning|info", "message": "...", "code": "..."}

    Includes retry/backoff + model fallbacks for 503 overload.
    Includes optional local faster-whisper fallback on persistent overload.
    """

    def __init__(self):
        self.q_in: "queue.Queue[tuple[bytes, str]]" = queue.Queue()
        self.q_out: "queue.Queue[dict]" = queue.Queue()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, pcm_segment: bytes, lang: str):
        self.q_in.put((pcm_segment, lang))

    def get_event(self, timeout: float = 0.01) -> Optional[dict]:
        try:
            return self.q_out.get(timeout=timeout)
        except queue.Empty:
            return None

    def _emit_status(self, message: str, level: str = "warning", code: str = "STT_DEGRADED"):
        self.q_out.put({"type": "status", "level": level, "message": message, "code": code, "ts": time.time()})

    def _emit_final(self, text: str, engine: str):
        self.q_out.put({"type": "final", "text": text, "engine": engine, "ts": time.time()})

    def _run(self):
        # Reuse one client per worker thread to reduce overhead
        try:
            client = _gemini_client()
        except Exception:
            logger.exception("Failed to initialize Gemini client in worker")
            client = None

        while not self.stop.is_set():
            try:
                pcm_segment, lang = self.q_in.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                if client is None:
                    client = _gemini_client()

                wav_bytes = write_wav_bytes(pcm_segment, sample_rate=SAMPLE_RATE)
                prompt = _lang_prompt(lang)

                audio_part = genai_types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")

                contents = [
                    genai_types.Content(
                        role="user",
                        parts=[
                            genai_types.Part.from_text(text=prompt),
                            audio_part,
                        ],
                    )
                ]

                def _on_retry(model: str, attempt: int, max_attempts: int, sleep_s: float):
                    # Tell the browser this may be delayed
                    self._emit_status(
                        message=f"STT delayed: Gemini model overloaded; retrying ({attempt}/{max_attempts})…",
                        level="warning",
                        code="STT_RETRYING",
                    )

                def _on_switch(new_model: str):
                    self._emit_status(
                        message=f"STT delayed: switching to fallback model ({new_model})…",
                        level="warning",
                        code="STT_MODEL_FALLBACK",
                    )

                try:
                    resp = gemini_generate_with_retry(
                        client=client,
                        contents=contents,
                        config=genai_types.GenerateContentConfig(temperature=0.0),
                        on_retry=_on_retry,
                        on_model_switch=_on_switch,
                    )
                    text = (resp.text or "").strip()
                    if text:
                        self._emit_final(text=text, engine=_effective_engine_name())
                        # Clear banner hint once we get a clean final
                        self._emit_status(message="STT recovered.", level="info", code="STT_OK")
                        continue

                except Exception as e:
                    # If overload persists: fallback locally (optional)
                    if _is_overload_error(e):
                        if USE_LOCAL_WHISPER_FALLBACK and _get_whisper_model() is not None:
                            self._emit_status(
                                message="STT degraded: Gemini is overloaded; using local Whisper fallback.",
                                level="warning",
                                code="STT_FALLBACK_LOCAL",
                            )
                            local_text = whisper_transcribe_pcm16(pcm_segment, lang)
                            if local_text:
                                self._emit_final(text=local_text, engine="local/faster-whisper")
                                self._emit_status(message="STT recovered.", level="info", code="STT_OK")
                                continue

                        # If no fallback, surface degraded status (but don't kill the worker)
                        self._emit_status(
                            message="STT degraded: Gemini is overloaded and no fallback is available. You may miss a few seconds.",
                            level="warning",
                            code="STT_DEGRADED",
                        )
                        continue

                    # Non-overload errors: log + surface a status (still keep worker alive)
                    logger.exception("Gemini live transcription worker failed")
                    self._emit_status(
                        message="STT error: transcription failed for a segment.",
                        level="warning",
                        code="STT_ERROR",
                    )

            except Exception:
                logger.exception("Gemini live transcription worker failed")
                self._emit_status(
                    message="STT error: worker failure.",
                    level="warning",
                    code="STT_ERROR",
                )


def register_ws_routes(sock):
    @sock.route("/ws/stt")
    def stt(ws):
        lang = parse_lang_query(ws.environ.get("QUERY_STRING") or "")
        vad = webrtcvad.Vad(STT_VAD_AGGRESSIVENESS)
        worker = GeminiWorker()

        ff = start_ffmpeg_decoder()
        stop = threading.Event()
        pcm_q: "queue.Queue[bytes]" = queue.Queue()

        def read_pcm():
            try:
                chunk_bytes = int(SAMPLE_RATE * 0.1) * 2
                while not stop.is_set():
                    data = ff.stdout.read(chunk_bytes)
                    if not data:
                        break
                    pcm_q.put(data)
            finally:
                stop.set()

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

        segment = bytearray()
        last_voiced_ts = time.time()
        seg_start_ts = time.time()
        last_partial_emit = 0.0

        # Tell UI we're ready
        try:
            ws.send(json.dumps({"type": "status", "level": "info", "code": "STT_OK", "message": "STT ready.", "engine": _effective_engine_name()}))
        except Exception:
            pass

        try:
            while not stop.is_set():
                try:
                    block = pcm_q.get(timeout=0.5)
                except queue.Empty:
                    # drain events
                    evt = worker.get_event(timeout=0.001)
                    if evt:
                        ws.send(json.dumps(evt))
                    continue

                segment += block

                voiced_ratio = vad_voiced_ratio(bytes(segment), SAMPLE_RATE, vad, VAD_FRAME_MS)
                audio_f32 = pcm_s16le_bytes_to_float32(bytes(segment))
                rms = rms_level_f32(audio_f32)

                now = time.time()
                is_voiced = voiced_ratio >= VAD_VOICED_RATIO_MIN and rms > 0.002
                if is_voiced:
                    last_voiced_ts = now

                # Optional partials: submit the whole buffer occasionally while voice is active.
                if EMIT_PARTIALS:
                    if (now - last_partial_emit) * 1000.0 >= STT_PARTIAL_MIN_INTERVAL_MS:
                        if len(segment) >= int(SAMPLE_RATE * 0.8) * 2 and is_voiced:
                            worker.submit(bytes(segment), lang)
                            last_partial_emit = now

                silence_ms = (now - last_voiced_ts) * 1000.0
                seg_ms = (now - seg_start_ts) * 1000.0

                should_finalize = (
                    (silence_ms >= STT_SEGMENT_SILENCE_MS and len(segment) >= int(SAMPLE_RATE * 0.35) * 2)
                    or (seg_ms >= STT_MAX_SEGMENT_MS)
                )

                if should_finalize:
                    # Only submit if there's some speechy content
                    if vad_voiced_ratio(bytes(segment), SAMPLE_RATE, vad, VAD_FRAME_MS) >= 0.15:
                        worker.submit(bytes(segment), lang)

                    segment.clear()
                    seg_start_ts = time.time()
                    last_voiced_ts = time.time()
                    last_partial_emit = 0.0

                # Drain worker events
                while True:
                    evt = worker.get_event(timeout=0.001)
                    if not evt:
                        break
                    ws.send(json.dumps(evt))

        finally:
            stop.set()
            try:
                worker.stop.set()
            except Exception:
                pass
            try:
                ff.terminate()
            except Exception:
                pass
