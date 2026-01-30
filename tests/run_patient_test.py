"""
Run patient history logic manually (no pytest). Verifies conversation patient_id and labels.
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def main():
    init_db()

    db = SessionLocal()
    try:
        clinician_role = db.query(Role).filter_by(name="clinician").first()
        if not clinician_role:
            clinician_role = Role(name="clinician")
            db.add(clinician_role)
            db.commit()
        u = User(
            email="manual_test@example.com",
            username="manual_test_user",
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

    patient_id = create_patient(identifier="P001", clinician_id=user_id)
    print(f"Created patient id={patient_id}")

    cid = create_conversation(owner_user_id=user_id, patient_id=patient_id)
    print(f"Created conversation id={cid} with patient_id={patient_id}")

    convos = list_conversations_for_user(user_id)
    assert len(convos) >= 1
    c = convos[0]
    print(f"List: conversation patient_id={c.patient_id!r} (type={type(c.patient_id).__name__})")

    patients = list_patients_for_user(user_id)
    print(f"List: patients count={len(patients)}, ids={[p.id for p in patients]}")
    plabels = {p.id: f"Patient {i + 1}" for i, p in enumerate(patients)}
    print(f"plabels={plabels}")

    pid = c.patient_id
    patient_label = plabels.get(int(pid)) if pid is not None else None
    if pid is not None and not patient_label:
        patient_label = "Patient"
    print(f"patient_label={patient_label!r}")

    if patient_label != "Patient 1":
        print("FAIL: expected 'Patient 1'")
        return 1
    print("PASS: patient label is 'Patient 1'")

    # Test update path
    cid2 = create_conversation(owner_user_id=user_id, patient_id=None)
    updated = update_conversation_patient(cid2, user_id, patient_id)
    print(f"Updated conversation {cid2}: {updated}")
    convos2 = list_conversations_for_user(user_id)
    c2 = next((x for x in convos2 if x.id == cid2), None)
    print(f"After update: c2.patient_id={c2.patient_id!r}, label={plabels.get(int(c2.patient_id)) if c2.patient_id else None!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
