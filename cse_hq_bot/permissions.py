from cse_hq_bot.errors import PermissionDeniedError
from cse_hq_bot.models import Actor, Role

LEADERSHIP_ROLES = {Role.LEADER, Role.CO_LEAD}



def ensure_can_manage_project(actor: Actor) -> None:
    if actor.role not in LEADERSHIP_ROLES:
        raise PermissionDeniedError("Only admins can manage project settings")



def ensure_can_modify_task(actor: Actor, task_owner_id: str | None, created_by: str) -> None:
    if actor.role in LEADERSHIP_ROLES:
        return
    if actor.user_id in {task_owner_id, created_by}:
        return
    raise PermissionDeniedError("Members may only modify their own or assigned tasks")



def ensure_can_modify_bug(actor: Actor, assignee_id: str | None, created_by: str) -> None:
    if actor.role in LEADERSHIP_ROLES:
        return
    if actor.user_id in {assignee_id, created_by}:
        return
    raise PermissionDeniedError("Members may only modify bugs they created or are assigned to")


def ensure_can_manage_meetings(actor: Actor) -> None:
    ensure_can_manage_project(actor)


def ensure_can_manage_decisions(actor: Actor) -> None:
    ensure_can_manage_project(actor)


def ensure_can_view_member_context(actor: Actor, target_user_id: str) -> None:
    if actor.role in LEADERSHIP_ROLES or actor.user_id == target_user_id:
        return
    raise PermissionDeniedError("You are not allowed to view another member's work context")


def ensure_can_view_standup(actor: Actor, user_id: str) -> None:
    if actor.role in LEADERSHIP_ROLES or actor.user_id == user_id:
        return
    raise PermissionDeniedError("You are not allowed to view this standup")
