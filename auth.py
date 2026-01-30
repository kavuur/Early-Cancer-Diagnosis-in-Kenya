from flask import Blueprint, jsonify, request, redirect, url_for, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import SessionLocal, User, Role, user_roles
from security import hash_password, verify_password

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
login_manager = LoginManager()

@login_manager.user_loader
def load_user(user_id):
    db = SessionLocal()
    try:
        return db.get(User, int(user_id))
    finally:
        db.close()

def grant_role(db, user: User, role_name: str):
    role = db.query(Role).filter_by(name=role_name).first()
    if role and not any(r.id == role.id for r in user.roles):
        db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
        db.commit()

@auth_bp.post("/signup")  # switch to invite-only later if you want
def signup():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    username = (data.get("username") or "").strip()
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"}), 400
    if username:
        if len(username) < 2 or len(username) > 64:
            return jsonify({"ok": False, "error": "Username must be 2–64 characters"}), 400
        if not all(c.isalnum() or c in "._-" for c in username):
            return jsonify({"ok": False, "error": "Username may only contain letters, numbers, . _ -"}), 400
    db = SessionLocal()
    try:
        if db.query(User).filter_by(email=email).first():
            return jsonify({"ok": False, "error": "Email already registered"}), 409
        if username and db.query(User).filter_by(username=username).first():
            return jsonify({"ok": False, "error": "Username already taken"}), 409
        u = User(
            email=email,
            username=username or None,
            password_hash=hash_password(password),
            email_verified=False,
        )
        db.add(u)
        db.commit()
        grant_role(db, u, "clinician")
        return jsonify({"ok": True})
    finally:
        db.close()

@auth_bp.post("/login")
def login():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(email=email).first()
        if not u or not verify_password(u.password_hash, password) or not u.is_active:
            return jsonify({"ok": False, "error": "Invalid credentials"}), 401
        login_user(u, remember=bool(data.get("remember")))
        _clear_conversation_session()
        return jsonify({
            "ok": True,
            "user": {
                "email": u.email,
                "username": u.username or (u.email.split("@")[0] if u.email else "User"),
                "roles": [r.name for r in u.roles],
            },
        })
    finally:
        db.close()

def _clear_conversation_session():
    """Clear conversation state so next user/session gets a fresh conversation."""
    session.pop("id", None)
    session.pop("conv", None)
    session.pop("patient_id", None)


@auth_bp.post("/logout")
@login_required
def logout():
    _clear_conversation_session()
    logout_user()
    return jsonify({"ok": True})


@auth_bp.get("/logout")
@login_required
def logout_redirect():
    """GET /auth/logout: log out and redirect to home (for nav link)."""
    _clear_conversation_session()
    logout_user()
    return redirect(url_for("index"))

@auth_bp.get("/me")
def me():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False})
    username = current_user.username or (current_user.email.split("@")[0] if current_user.email else "User")
    return jsonify({
        "authenticated": True,
        "user": {"email": current_user.email, "username": username, "roles": [r.name for r in current_user.roles]},
    })
