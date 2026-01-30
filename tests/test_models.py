import os
import sys

# Ensure project root is on sys.path so we can import app modules
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models import init_db, create_conversation, list_conversations


def test_create_conversation_persists_in_db():
    """
    Basic sanity test:
    - DB schema can be created
    - create_conversation() returns an ID
    - the created conversation is queryable from the database
    """
    # Ensure tables exist
    init_db()

    # Create a new conversation
    cid = create_conversation()

    # Returned ID should be a non-empty string
    assert isinstance(cid, str)
    assert cid

    # It should be present in the list of conversations
    conversations = list_conversations()
    ids = {c.id for c in conversations}
    assert cid in ids

