# admin.py
from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from sqlalchemy import func, desc, or_
from collections import Counter, defaultdict
import re

from models import (
    SessionLocal,
    Conversation,
    Message,
    User,
    Role,
    user_roles,
    Patient,
    create_patient,
    get_next_global_patient_identifier,
    delete_conversation_by_id,
)

# Optional: FAISS-driven disease likelihoods
from medical_case_faiss import MedicalCaseFAISS

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# --------------------------
# Auth guards
# --------------------------
def _require_admin():
    return current_user.is_authenticated and any(r.name == "admin" for r in current_user.roles)

def admin_guard():
    if not _require_admin():
        return jsonify({"ok": False, "error": "Admin only"}), 403


def _user_display_name(user) -> str:
    """Return display name for admin: username or email prefix (no raw email)."""
    if user is None:
        return "—"
    return (user.username or "").strip() or (
        (user.email.split("@")[0] if user.email else "User")
    )


# --------------------------
# Helpers: text cleaning & symptom extraction
# --------------------------
# For pulling a single target symptom from recommender text (your existing heuristic)
SYM_RE = re.compile(r"(?:symptom|target|focus)\s*:\s*([A-Za-z][\w\s/-]{1,80})", re.IGNORECASE)

def _extract_symptom(text: str) -> str | None:
    if not text:
        return None
    m = SYM_RE.search(text)
    if m:
        return m.group(1).strip()
    for kw in (
        "headache", "chest pain", "cough", "wheezing", "shortness of breath",
        "fever", "nausea", "dizziness", "fatigue", "joint pain"
    ):
        if kw in text.lower():
            return kw
    return None

# Strip legacy HTML if any
TAG_RE = re.compile(r"<[^>]+>")

def _safe_text(m: Message) -> str:
    msg = getattr(m, "message", "") or ""
    return TAG_RE.sub("", msg)

# Counter-based symptom extraction for tallies/graphs
SYMPTOM_LEXICON = [
    "fever","cough","wheezing","shortness of breath","breathlessness","chest pain","headache",
    "nausea","vomiting","fatigue","dizziness","joint pain","swelling","stiffness","back pain",
    "sore throat","runny nose","rash","abdominal pain","diarrhea","constipation","weight loss",
    "night sweats","palpitations","fainting","tingling","numbness","weakness","pain"
]
CANON = {s: s for s in SYMPTOM_LEXICON}
CANON.update({
    "sob": "shortness of breath",
    "dyspnea": "shortness of breath",
    "tiredness": "fatigue",
    "lightheadedness": "dizziness",
    "chest tightness": "chest pain",
    "loose stools": "diarrhea",
    "constipated": "constipation",
    "weightloss": "weight loss",
})

def extract_symptoms(text: str) -> Counter:
    t = " " + (text or "").lower() + " "
    counts = Counter()
    # phrase-first to catch multi-word entries
    for phrase in sorted(CANON.keys(), key=len, reverse=True):
        pattern = r'\b' + re.escape(phrase) + r'\b'
        hits = re.findall(pattern, t)
        if hits:
            counts[CANON[phrase]] += len(hits)
            # remove to avoid double-counting overlaps
            t = re.sub(pattern, " ", t)
    return counts

# Lazy FAISS loader for disease likelihoods
_faiss = None
def get_faiss():
    global _faiss
    if _faiss is None:
        idx_path  = current_app.config.get('FAISS_INDEX_PATH', 'medical_cases.index')
        meta_path = current_app.config.get('FAISS_METADATA_PATH', 'medical_cases_metadata.pkl')
        f = MedicalCaseFAISS()
        f.load_index(idx_path, meta_path)
        _faiss = f
    return _faiss

# --------------------------
# Overview stats
# --------------------------
@admin_bp.get("/api/summary")
@login_required
def summary():
    if not _require_admin():
        return admin_guard()

    db = SessionLocal()
    try:
        total_users = db.query(User).count()
        clinicians = (
            db.query(User)
              .join(user_roles, user_roles.c.user_id == User.id)
              .join(Role, Role.id == user_roles.c.role_id)
              .filter(Role.name == "clinician")
              .count()
        )
        admins = (
            db.query(User)
              .join(user_roles, user_roles.c.user_id == User.id)
              .join(Role, Role.id == user_roles.c.role_id)
              .filter(Role.name == "admin")
              .count()
        )
        total_convos = db.query(Conversation).count()
        total_messages = db.query(Message).count()
        patient_msgs = db.query(Message).filter(Message.role == "patient").count()
        clinician_msgs = db.query(Message).filter(Message.role == "clinician").count()
        rec_questions = db.query(Message).filter(Message.type == "question_recommender").count()

        # Conversations per day (last 30 rows by date asc)
        convs_per_day = (
            db.query(func.date(Conversation.created_at), func.count(Conversation.id))
              .group_by(func.date(Conversation.created_at))
              .order_by(func.date(Conversation.created_at))
              .limit(30)
              .all()
        )

        # Top clinicians by # of conversations (Conversation.owner_user_id, not ConversationOwner)
        top_clinician_rows = (
            db.query(User, func.count(Conversation.id).label("cnt"))
              .join(user_roles, user_roles.c.user_id == User.id)
              .join(Role, Role.id == user_roles.c.role_id)
              .filter(Role.name == "clinician")
              .outerjoin(Conversation, Conversation.owner_user_id == User.id)
              .group_by(User.id, User.email, User.username)
              .order_by(desc(func.count(Conversation.id)))
              .limit(10)
              .all()
        )
        top_clinicians = [
            {"display_name": _user_display_name(u), "count": c}
            for u, c in top_clinician_rows
        ]

        return jsonify({
            "ok": True,
            "users": {"total": total_users, "clinicians": clinicians, "admins": admins},
            "conversations": {"total": total_convos},
            "messages": {
                "total": total_messages,
                "patient": patient_msgs,
                "clinician": clinician_msgs,
                "recommended": rec_questions
            },
            "series": {
                "conversations_per_day": [[str(d), c] for d, c in convs_per_day],
                "top_clinicians": top_clinicians,
            }
        })
    finally:
        db.close()

# --------------------------
# List clinicians (with conversation counts)
# --------------------------
@admin_bp.get("/api/clinicians")
@login_required
def clinicians():
    if not _require_admin():
        return admin_guard()

    db = SessionLocal()
    try:
        # Count conversations by Conversation.owner_user_id (not ConversationOwner table)
        rows = (
            db.query(User, func.count(Conversation.id).label("convos"))
              .join(user_roles, user_roles.c.user_id == User.id)
              .join(Role, Role.id == user_roles.c.role_id)
              .filter(Role.name == "clinician")
              .outerjoin(Conversation, Conversation.owner_user_id == User.id)
              .group_by(User.id, User.email, User.username)
              .order_by(desc("convos"))
              .all()
        )
        return jsonify({"ok": True, "clinicians": [
            {"id": u.id, "display_name": _user_display_name(u), "conversations": c}
            for u, c in rows
        ]})
    finally:
        db.close()

# --------------------------
# Paginated conversations (owner display name, patient label; optional clinician filter)
# --------------------------
@admin_bp.get("/api/conversations")
@login_required
def conversations():
    """Paginated list of all conversations with owner display name and patient label (admin-only)."""
    if not _require_admin():
        return admin_guard()

    page = int(request.args.get("page", 1))
    size = min(int(request.args.get("size", 20)), 100)
    clinician_id = request.args.get("clinician_id", type=int)
    offset = (page - 1) * size

    db = SessionLocal()
    try:
        q = db.query(Conversation)
        if clinician_id is not None:
            q = q.filter(Conversation.owner_user_id == clinician_id)
        total = q.count()

        rows = (
            db.query(
                Conversation.id,
                Conversation.created_at,
                User.email,
                User.username,
                Conversation.owner_user_id,
                Conversation.patient_id,
                func.count(Message.id).label("message_count"),
            )
            .outerjoin(User, User.id == Conversation.owner_user_id)
            .outerjoin(Message, Message.conversation_id == Conversation.id)
        )
        if clinician_id is not None:
            rows = rows.filter(Conversation.owner_user_id == clinician_id)
        rows = (
            rows.group_by(
                Conversation.id,
                Conversation.created_at,
                User.email,
                User.username,
                Conversation.owner_user_id,
                Conversation.patient_id,
            )
            .order_by(Conversation.created_at.desc())
            .offset(offset)
            .limit(size)
            .all()
        )

        # Build per-clinician patient label maps so admin sees Patient 1, Patient 2, ...
        owner_ids = {owner_id for (_cid, _created, _email, _username, owner_id, _pid, _mc) in rows if owner_id}
        patient_labels_by_owner: dict[int, dict[int, str]] = {}
        for oid in owner_ids:
            patients = (
                db.query(Patient)
                  .filter(Patient.clinician_id == oid)
                  .order_by(Patient.id.asc())
                  .all()
            )
            patient_labels_by_owner[oid] = {
                p.id: f"Patient {i + 1}" for i, p in enumerate(patients)
            }

        convs = []
        for (cid, created, email, username, owner_id, patient_id, msg_count) in rows:
            display_name = (username or "").strip() or (
                (email.split("@")[0] if email else "User")
            ) if (email or username) else (str(owner_id) if owner_id is not None else "—")
            patient_label = "—"
            if patient_id and owner_id:
                labels_for_owner = patient_labels_by_owner.get(owner_id, {})
                patient_label = labels_for_owner.get(patient_id) or "Patient"

            convs.append({
                "id": cid,
                "created_at": created.isoformat(),
                "owner_display_name": display_name,
                "owner_user_id": owner_id,
                "patient_id": patient_id,
                "patient_label": patient_label,
                "message_count": msg_count,
            })

        return jsonify({"ok": True, "page": page, "size": size, "total": total, "conversations": convs})
    finally:
        db.close()


@admin_bp.delete("/api/conversation/<cid>")
@login_required
def delete_conversation(cid):
    """Delete a conversation (and its messages) as admin."""
    if not _require_admin():
        return admin_guard()
    ok = delete_conversation_by_id(cid)
    if not ok:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True})


# --------------------------
# User Management
# --------------------------
@admin_bp.get("/api/users")
@login_required
def list_users():
    """List all users with their roles."""
    if not _require_admin():
        return admin_guard()

    db = SessionLocal()
    try:
        users = db.query(User).order_by(User.id.desc()).all()
        result = []
        for u in users:
            result.append({
                "id": u.id,
                "email": u.email,
                "username": u.username,
                "display_name": _user_display_name(u),
                "roles": [r.name for r in u.roles],
                "created_at": u.created_at.isoformat() if hasattr(u, 'created_at') and u.created_at else None
            })
        return jsonify({"ok": True, "users": result})
    finally:
        db.close()


@admin_bp.post("/api/users")
@login_required
def create_user():
    """Create a new user with roles."""
    if not _require_admin():
        return admin_guard()

    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    role_names = data.get("roles") or []

    if not email:
        return jsonify({"ok": False, "error": "Email is required"}), 400
    if not password:
        return jsonify({"ok": False, "error": "Password is required"}), 400

    db = SessionLocal()
    try:
        # Check if user already exists
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            return jsonify({"ok": False, "error": "User with this email already exists"}), 400

        # Create user
        from security import hash_password
        new_user = User(
            email=email,
            username=username or None,
            password_hash=hash_password(password)
        )
        db.add(new_user)
        db.flush()

        # Assign roles
        for role_name in role_names:
            role = db.query(Role).filter(Role.name == role_name).first()
            if role:
                new_user.roles.append(role)

        db.commit()
        return jsonify({
            "ok": True,
            "user_id": new_user.id,
            "email": new_user.email
        })
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@admin_bp.put("/api/users/<int:user_id>")
@login_required
def update_user(user_id):
    """Update user roles."""
    if not _require_admin():
        return admin_guard()

    data = request.get_json(force=True, silent=True) or {}
    role_names = data.get("roles") or []
    username = data.get("username")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        # Update username if provided
        if username is not None:
            user.username = username.strip() or None

        # Update roles
        user.roles = []
        for role_name in role_names:
            role = db.query(Role).filter(Role.name == role_name).first()
            if role:
                user.roles.append(role)

        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@admin_bp.delete("/api/users/<int:user_id>")
@login_required
def delete_user(user_id):
    """Delete a user."""
    if not _require_admin():
        return admin_guard()

    # Prevent deleting yourself
    if current_user.id == user_id:
        return jsonify({"ok": False, "error": "Cannot delete your own account"}), 400

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return jsonify({"ok": False, "error": "User not found"}), 404

        db.delete(user)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@admin_bp.get("/api/roles")
@login_required
def list_roles():
    """List all available roles."""
    if not _require_admin():
        return admin_guard()

    db = SessionLocal()
    try:
        roles = db.query(Role).all()
        return jsonify({
            "ok": True,
            "roles": [{"id": r.id, "name": r.name} for r in roles]
        })
    finally:
        db.close()


# --------------------------
# Admin: create patient (identifier continues from latest in DB)
# --------------------------
@admin_bp.post("/api/patients")
@login_required
def admin_create_patient():
    """Create a patient; assign to a clinician. Identifier is next global P001, P002, ..."""
    if not _require_admin():
        return admin_guard()
    data = request.get_json(force=True, silent=True) or {}
    clinician_id = data.get("clinician_id")
    if clinician_id is None:
        return jsonify({"ok": False, "error": "clinician_id required"}), 400
    try:
        clinician_id = int(clinician_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "clinician_id must be an integer"}), 400
    identifier = (data.get("identifier") or "").strip()
    if not identifier:
        identifier = get_next_global_patient_identifier()
    display_name = (data.get("display_name") or "").strip() or None
    pid = create_patient(identifier=identifier, clinician_id=clinician_id, display_name=display_name)
    return jsonify({"ok": True, "patient_id": pid, "identifier": identifier})


# --------------------------
# Conversation detail (messages + recommended questions)
# --------------------------
@admin_bp.get("/api/conversation/<cid>")
@login_required
def conversation_detail(cid):
    if not _require_admin():
        return admin_guard()

    db = SessionLocal()
    try:
        msgs = (
            db.query(Message)
              .filter(Message.conversation_id == cid)
              .order_by(Message.created_at.asc())
              .all()
        )

        out_msgs, recos = [], []
        for m in msgs:
            text = _safe_text(m)
            out_msgs.append({
                "id": m.id,
                "role": m.role,
                "type": m.type,
                "text": text,
                "timestamp": m.timestamp,
                "created_at": m.created_at.isoformat(),
            })
            if (m.type == "question_recommender") or (m.role == "Question Recommender"):
                recos.append({
                    "id": m.id,
                    "question": text,
                    "symptom": _extract_symptom(text)
                })

        return jsonify({"ok": True, "messages": out_msgs, "recommended_questions": recos})
    finally:
        db.close()

# --------------------------
# Symptom tallies (global + per-conversation)
# --------------------------
@admin_bp.get("/api/symptoms")
@login_required
def symptoms_api():
    if not _require_admin():
        return admin_guard()

    db = SessionLocal()
    try:
        # Pull all conversations + owners in one pass (display name, no email)
        convo_rows = (
            db.query(
                Conversation.id,
                Conversation.created_at,
                User.email,
                User.username,
                Conversation.owner_user_id,
            )
            .outerjoin(User, User.id == Conversation.owner_user_id)
            .order_by(Conversation.created_at.desc())
            .all()
        )
        conv_ids = [cid for (cid, _created, _email, _un, _uid) in convo_rows]

        def _display(e, un, uid):
            return (un or "").strip() or ((e.split("@")[0] if e else "User")) if (e or un) else (str(uid) if uid is not None else "—")

        owner_map = {
            cid: {
                "display_name": _display(email, username, uid),
                "id": uid,
                "created_at": created.isoformat(),
            }
            for (cid, created, email, username, uid) in convo_rows
        }

        # No conversations yet
        if not conv_ids:
            return jsonify({"ok": True, "global": {}, "by_conversation": []})

        # Only patient utterances for counting (be forgiving on casing)
        from sqlalchemy import or_
        msgs = (
            db.query(Message)
              .filter(Message.conversation_id.in_(conv_ids))
              .filter(or_(Message.role == "patient", Message.role == "Patient"))
              .order_by(Message.created_at.asc())
              .all()
        )

        from collections import Counter, defaultdict
        global_counts = Counter()
        per_conv = defaultdict(Counter)

        for m in msgs:
            counts = extract_symptoms(m.message or "")  # uses your helper defined above
            global_counts.update(counts)
            per_conv[m.conversation_id].update(counts)

        by_conv = []
        for cid in conv_ids:
            meta = owner_map.get(cid, {})
            by_conv.append({
                "conversation_id": cid,
                "owner_display_name": meta.get("display_name", "—"),
                "owner_user_id": meta.get("id"),
                "created_at": meta.get("created_at"),
                "symptoms": dict(per_conv[cid].most_common()),
            })

        return jsonify({
            "ok": True,
            "global": dict(global_counts.most_common()),
            "by_conversation": by_conv
        })
    finally:
        db.close()


# --------------------------
# Disease likelihoods per conversation (FAISS-weighted)
# --------------------------
@admin_bp.get("/api/conversation/<cid>/disease_likelihoods")
@login_required
def conversation_disease_likelihoods(cid):
    if not _require_admin():
        return admin_guard()

    db = SessionLocal()
    try:
        msgs = (
            db.query(Message)
              .filter(Message.conversation_id == cid)
              .order_by(Message.created_at.asc())
              .all()
        )
        if not msgs:
            return jsonify({"ok": False, "error": "No messages for conversation"}), 404

        # Prefer patient text; fall back to full transcript
        patient_text = " ".join((m.message or "") for m in msgs if (m.role or "").lower() == "patient").strip()
        if not patient_text:
            patient_text = " ".join((m.message or "") for m in msgs if m.message).strip()

        f = get_faiss()
        # be lenient to get a spread of candidates
        results = f.search_similar_cases(patient_text, k=8, similarity_threshold=0.05)

        weights = defaultdict(float)
        for r in results:
            sim = max(float(r.similarity_score), 0.0)
            sus = r.Suspected_illness or {}
            if isinstance(sus, dict):
                for disease, _val in sus.items():
                    if (disease or "").strip():
                        weights[disease.strip()] += sim
            elif isinstance(sus, str) and sus.strip():
                weights[sus.strip()] += sim

        total = sum(weights.values()) or 1.0
        ranked = sorted(
            ({"disease": k, "weight": v, "likelihood_pct": round(100.0 * v / total, 1)} for k, v in weights.items()),
            key=lambda x: (-x["weight"], x["disease"])
        )[:5]

        sym = extract_symptoms(patient_text)

        return jsonify({
            "ok": True,
            "conversation_id": cid,
            "symptoms": dict(sym.most_common()),
            "top_diseases": ranked,
            "faiss_matches": [{
                "case_id": r.case_id,
                "similarity": round(float(r.similarity_score), 4),
                "suspected": r.Suspected_illness
            } for r in results]
        })
    finally:
        db.close()
