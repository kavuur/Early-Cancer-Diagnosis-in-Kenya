# models.py
import os
import re
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, Integer
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, scoped_session, joinedload
import uuid as _uuid

# --- Config ---
DB_URL = os.getenv("DATABASE_URL", "sqlite:///app.db")

# --- SQLAlchemy setup ---
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()

# --- Models ---
class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)               # uuid string
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), index=True, nullable=True)
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at.asc()",
    )

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True)               # uuid string
    conversation_id = Column(String, ForeignKey("conversations.id"), index=True, nullable=False)
    role = Column(String, index=True)                   # patient|clinician|listener|Question Recommender
    type = Column(String, default="message")            # message|question_recommender
    message = Column(Text, nullable=True)
    timestamp = Column(String, nullable=True)           # keep your "HH:MM:SS"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")

    # --- ADD: Auth models ---
from sqlalchemy import Integer, Boolean, Table, UniqueConstraint

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
    UniqueConstraint("user_id", "role_id", name="uq_user_role"),
)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    username = Column(String(64), unique=True, nullable=True, index=True)  # user-chosen display name
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    email_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    roles = relationship("Role", secondary=user_roles, back_populates="users", lazy="joined")

    # Flask-Login helpers
    @property
    def is_authenticated(self): return True
    @property
    def is_anonymous(self): return False
    def get_id(self): return str(self.id)

    def has_role(self, name: str) -> bool:
        return any(r.name == name for r in self.roles)

class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(32), unique=True, nullable=False)  # "clinician", "admin"
    users = relationship("User", secondary=user_roles, back_populates="roles")

class ConversationOwner(Base):
    __tablename__ = "conversation_owners"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), index=True, nullable=False, unique=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)


class Patient(Base):
    __tablename__ = "patients"
    id = Column(Integer, primary_key=True)
    identifier = Column(String(128), nullable=False)   # e.g. "P001", "Case 123"
    display_name = Column(String(255), nullable=True) # optional label
    clinician_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)  # owning clinician
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    conversations = relationship("Conversation", back_populates="patient", lazy="dynamic")


# Conversation -> Patient relationship (patient_id already on Conversation)
Conversation.patient = relationship("Patient", back_populates="conversations")


# --- Init / helpers ---
def _migrate_add_patient_fk():
    """Ensure patients table exists and conversations.patient_id exists (for existing DBs)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "patients" not in insp.get_table_names():
        Base.metadata.tables["patients"].create(engine, checkfirst=True)
    if "conversations" in insp.get_table_names():
        cols = [c["name"] for c in insp.get_columns("conversations")]
        if "patient_id" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE conversations ADD COLUMN patient_id INTEGER"))
                conn.commit()


def _migrate_add_user_username():
    """Add users.username if missing (for existing DBs)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "users" not in insp.get_table_names():
        return
    cols = [c["name"] for c in insp.get_columns("users")]
    if "username" not in cols:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(64)"))
            conn.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_add_patient_fk()
    _migrate_add_user_username()
    _seed_roles()

def _seed_roles():
    db = SessionLocal()
    try:
        existing = {r.name for r in db.query(Role).all()}
        for name in ("clinician", "admin"):
            if name not in existing:
                db.add(Role(name=name))
        db.commit()
    finally:
        db.close()


def create_conversation(owner_user_id: int | None = None, patient_id: int | None = None) -> str:
    db = SessionLocal()
    try:
        cid = str(_uuid.uuid4())
        db.add(Conversation(id=cid, owner_user_id=owner_user_id, patient_id=patient_id))
        db.commit()
        return cid
    finally:
        db.close()

def log_message(conversation_id: str, role: str, message: str, timestamp: str, type_: str = "message"):
    """Insert a single message row."""
    db = SessionLocal()
    try:
        db.add(Message(
            id=str(_uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            type=type_,
            message=message,
            timestamp=timestamp
        ))
        db.commit()
    finally:
        db.close()

# admin helpers
def list_conversations():
    db = SessionLocal()
    try:
        return db.query(Conversation).order_by(Conversation.created_at.desc()).all()
    finally:
        db.close()


def list_conversations_for_user(user_id: int):
    """Conversations owned by this clinician, newest first (with patient loaded)."""
    db = SessionLocal()
    try:
        return (
            db.query(Conversation)
            .options(joinedload(Conversation.patient))
            .filter(Conversation.owner_user_id == user_id)
            .order_by(Conversation.created_at.desc())
            .all()
        )
    finally:
        db.close()


def get_conversation_if_owned_by(conversation_id: str, user_id: int):
    """Return conversation only if it belongs to this user (or None). Loads patient."""
    db = SessionLocal()
    try:
        c = (
            db.query(Conversation)
            .options(joinedload(Conversation.patient))
            .filter(
                Conversation.id == conversation_id,
                Conversation.owner_user_id == user_id,
            )
            .first()
        )
        return c
    finally:
        db.close()


def update_conversation_patient(conversation_id: str, user_id: int, patient_id: int | None) -> bool:
    """Set patient_id on this conversation if owned by user. Returns True if updated."""
    db = SessionLocal()
    try:
        n = (
            db.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.owner_user_id == user_id,
            )
            .update({Conversation.patient_id: patient_id}, synchronize_session=False)
        )
        db.commit()
        return n > 0
    finally:
        db.close()


def delete_conversation_by_id(conversation_id: str) -> bool:
    """Admin helper: delete a conversation (and its messages) regardless of owner."""
    db = SessionLocal()
    try:
        c = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if c is None:
            return False
        db.delete(c)
        db.commit()
        return True
    finally:
        db.close()


def delete_conversation_if_owned_by(conversation_id: str, user_id: int) -> bool:
    """Delete this conversation if owned by user (messages cascade). Returns True if deleted."""
    db = SessionLocal()
    try:
        c = (
            db.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.owner_user_id == user_id,
            )
            .first()
        )
        if c is None:
            return False
        db.delete(c)
        db.commit()
        return True
    finally:
        db.close()


# --- Patient helpers ---
_P_ID_RE = re.compile(r"^P(\d+)$", re.IGNORECASE)


def get_next_global_patient_identifier() -> str:
    """Next patient identifier (P001, P002, ...) from the latest in the DB, so counts continue globally across clinicians."""
    db = SessionLocal()
    try:
        rows = db.query(Patient.identifier).all()
        max_n = 0
        for (ident,) in rows:
            if not ident:
                continue
            m = _P_ID_RE.match(ident.strip())
            if m:
                max_n = max(max_n, int(m.group(1)))
        return f"P{max_n + 1:03d}"
    finally:
        db.close()


def list_patients_for_user(clinician_id: int):
    """Patients created by this clinician."""
    db = SessionLocal()
    try:
        return (
            db.query(Patient)
            .filter(Patient.clinician_id == clinician_id)
            .order_by(Patient.created_at.desc())
            .all()
        )
    finally:
        db.close()


def create_patient(identifier: str, clinician_id: int | None = None, display_name: str | None = None) -> int:
    """Create a patient; returns new patient id."""
    db = SessionLocal()
    try:
        p = Patient(identifier=identifier, clinician_id=clinician_id, display_name=display_name)
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


def get_patient(patient_id: int):
    """Return Patient by id or None."""
    db = SessionLocal()
    try:
        return db.query(Patient).filter(Patient.id == patient_id).first()
    finally:
        db.close()


def get_conversation_messages(conversation_id: str):
    db = SessionLocal()
    try:
        return (
            db.query(Message)
              .filter(Message.conversation_id == conversation_id)
              .order_by(Message.created_at.asc())
              .all()
        )
    finally:
        db.close()
