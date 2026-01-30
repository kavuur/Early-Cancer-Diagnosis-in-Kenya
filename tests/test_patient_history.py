"""
Test that conversation patient_id is persisted and that history shows "Patient 1", etc.

Run (with venv activated and deps installed):
  pytest tests/test_patient_history.py -v
  # or: python tests/run_patient_test.py  (no pytest)
Uses in-memory DB so it does not touch app.db.
"""
import os
import sys

# Use in-memory DB before any model import so engine is created with it
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models import (
    init_db,
    SessionLocal,
    User,
    Role,
    user_roles,
    create_conversation,
    create_patient,
    list_conversations_for_user,
    list_patients_for_user,
    update_conversation_patient,
)


def _build_patient_labels(patients):
    """Same logic as app._patient_labels_for_current_user (newest first)."""
    return {p.id: f"Patient {i + 1}" for i, p in enumerate(patients)}


def _patient_label_for_conversation(conversation, plabels):
    """Same logic as app api_my_conversations."""
    pid = conversation.patient_id
    patient_label = plabels.get(int(pid)) if pid is not None else None
    if pid is not None and not patient_label:
        patient_label = "Patient"
    return patient_label


def test_conversation_with_patient_shows_patient_label():
    """Create user, patient, conversation with patient_id; list should return 'Patient 1'."""
    init_db()

    db = SessionLocal()
    try:
        # Ensure clinician role exists
        clinician_role = db.query(Role).filter_by(name="clinician").first()
        if not clinician_role:
            clinician_role = Role(name="clinician")
            db.add(clinician_role)
            db.commit()
        # Create user
        u = User(
            email="history_test@example.com",
            username="history_test_user",
            password_hash="fake",
            email_verified=False,
        )
        db.add(u)
        db.commit()
        user_id = u.id
        # Link user to clinician role
        db.execute(user_roles.insert().values(user_id=user_id, role_id=clinician_role.id))
        db.commit()
    finally:
        db.close()

    # Create patient for this clinician
    patient_id = create_patient(identifier="P001", clinician_id=user_id)
    assert patient_id is not None

    # Create conversation with owner and patient
    cid = create_conversation(owner_user_id=user_id, patient_id=patient_id)
    assert cid

    # Same logic as api_my_conversations
    convos = list_conversations_for_user(user_id)
    assert len(convos) >= 1
    c = convos[0]
    assert c.id == cid
    assert c.patient_id is not None, "Conversation should have patient_id set"
    assert c.patient_id == patient_id

    patients = list_patients_for_user(user_id)
    assert len(patients) >= 1
    plabels = _build_patient_labels(patients)
    assert patient_id in plabels
    assert plabels[patient_id] == "Patient 1"

    patient_label = _patient_label_for_conversation(c, plabels)
    assert patient_label == "Patient 1", (
        f"Expected 'Patient 1', got patient_id={c.patient_id!r} plabels={plabels} label={patient_label!r}"
    )


def test_update_conversation_patient_then_list_shows_label():
    """Update a conversation's patient_id via update_conversation_patient; list should show label."""
    init_db()

    db = SessionLocal()
    try:
        clinician_role = db.query(Role).filter_by(name="clinician").first()
        if not clinician_role:
            clinician_role = Role(name="clinician")
            db.add(clinician_role)
            db.commit()
        u = User(
            email="update_test@example.com",
            username="update_test_user",
            password_hash="fake",
            email_verified=False,
        )
        db.add(u)
        db.commit()
        user_id = u.id
        db.execute(user_roles.insert().values(user_id=user_id, role_id=clinician_role.id))
        db.commit()
    finally:
        db.close()

    # Conversation created WITHOUT patient first
    cid = create_conversation(owner_user_id=user_id, patient_id=None)
    patient_id = create_patient(identifier="P002", clinician_id=user_id)

    # Simulate user selecting patient from dropdown: update current conversation
    updated = update_conversation_patient(cid, user_id, patient_id)
    assert updated is True

    convos = list_conversations_for_user(user_id)
    c = next((x for x in convos if x.id == cid), None)
    assert c is not None
    assert c.patient_id == patient_id

    patients = list_patients_for_user(user_id)
    plabels = _build_patient_labels(patients)
    label = _patient_label_for_conversation(c, plabels)
    assert label == "Patient 1" or label == "Patient 2", f"Expected numbered label, got {label!r}"
