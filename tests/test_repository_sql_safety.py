from pathlib import Path

import pytest

from cse_hq_bot.db import Database
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.repositories.task_repository import TaskRepository


def test_repository_updates_reject_unknown_identifiers_atomically(
    tmp_path: Path,
) -> None:
    db = Database(str(tmp_path / "sql-safety.db"))
    db.initialize()

    task_repo = TaskRepository(db)
    task_id = task_repo.create("Original task", "", 1, "member")
    with pytest.raises(ValueError, match="Unsupported task field"):
        task_repo.update(task_id, {"title": "Changed", "title = NULL": "unsafe"})
    assert task_repo.get(task_id)["title"] == "Original task"

    bug_repo = BugRepository(db)
    bug_id = bug_repo.create("Original bug", "", 1, "member")
    with pytest.raises(ValueError, match="Unsupported bug field"):
        bug_repo.update(bug_id, {"title": "Changed", "title = NULL": "unsafe"})
    assert bug_repo.get(bug_id)["title"] == "Original bug"

    project_repo = ProjectRepository(db)
    with pytest.raises(ValueError, match="Unsupported project field"):
        project_repo.update_management(
            {"name": "Changed", "name = NULL": "unsafe"}
        )
    assert project_repo.get_settings()["name"] == "CSE-HQ Project"

    collab_repo = CollaborationRepository(db)
    meeting_id = collab_repo.create_meeting(
        title="Original meeting",
        description="",
        agenda="",
        status="scheduled",
        created_by="member",
        scheduled_at="2026-09-21 10:00",
    )
    with pytest.raises(ValueError, match="Unsupported meeting field"):
        collab_repo.update_meeting(
            meeting_id, {"title": "Changed", "title = NULL": "unsafe"}
        )
    assert collab_repo.get_meeting(meeting_id)["title"] == "Original meeting"
