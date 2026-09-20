def task_code(task: dict) -> str:
    return str(task.get("code") or f"TASK-{int(task['id']):03d}")


def bug_code(bug: dict) -> str:
    return str(bug.get("code") or f"BUG-{int(bug['id']):03d}")


def meeting_code(meeting: dict) -> str:
    return str(meeting.get("code") or f"MEETING-{int(meeting['id']):03d}")


def decision_code(decision: dict) -> str:
    return str(decision.get("code") or f"DEC-{int(decision['id']):03d}")


def standup_code(standup: dict) -> str:
    return str(standup.get("code") or f"STANDUP-{int(standup['id']):03d}")
