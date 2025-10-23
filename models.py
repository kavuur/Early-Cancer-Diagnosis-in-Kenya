# models.py
import os
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Text, DateTime, ForeignKey, Integer
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, scoped_session
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


# --- Init / helpers ---
def init_db():
    Base.metadata.create_all(bind=engine)
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


def create_conversation(owner_user_id: int | None = None) -> str:
    db = SessionLocal()
    try:
        cid = str(_uuid.uuid4())
        db.add(Conversation(id=cid, owner_user_id=owner_user_id))
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
