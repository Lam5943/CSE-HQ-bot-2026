from types import SimpleNamespace

from cse_hq_bot.bot import build_ai_session_thread_name
from cse_hq_bot.db import Database
from cse_hq_bot.models import Actor, Role
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.services.ai_session_service import AISessionService


def test_ai_session_thread_name_is_human_friendly_and_bounded():
    assert build_ai_session_thread_name("Lâm", 1) == "session của Lâm #1"
    assert build_ai_session_thread_name("  Alice\nNguyen  ", 12) == (
        "session của Alice Nguyen #12"
    )

    long_name = build_ai_session_thread_name("x" * 200, 999)
    assert long_name.startswith("session của ")
    assert long_name.endswith(" #999")
    assert len(long_name) <= 80


def test_ai_session_number_is_scoped_per_user(tmp_path):
    db = Database(str(tmp_path / "sessions.db"))
    db.initialize()
    service = AISessionService(
        AISessionRepository(db),
        SimpleNamespace(),
        max_history_messages=4,
    )
    alice = Actor("alice", Role.MEMBER)
    bob = Actor("bob", Role.MEMBER)

    assert service.next_session_number(alice) == 1
    assert service.next_session_number(bob) == 1

    service.create_session(alice, "alice-thread-1")
    service.create_session(alice, "alice-thread-2")
    service.create_session(bob, "bob-thread-1")

    assert service.next_session_number(alice) == 3
    assert service.next_session_number(bob) == 2
