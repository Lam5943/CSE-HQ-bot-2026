from datetime import UTC, datetime

from cse_hq_bot.errors import InvalidInputError, InvalidTransitionError, NotFoundError, PermissionDeniedError
from cse_hq_bot.models import Actor, MeetingStatus, Role
from cse_hq_bot.permissions import ensure_can_manage_meetings
from cse_hq_bot.repositories.collab_repository import CollaborationRepository


class MeetingService:
    _TRANSITIONS: dict[str, set[str]] = {
        MeetingStatus.SCHEDULED.value: {
            MeetingStatus.IN_PROGRESS.value,
            MeetingStatus.CANCELLED.value,
        },
        MeetingStatus.IN_PROGRESS.value: {
            MeetingStatus.COMPLETED.value,
        },
        MeetingStatus.COMPLETED.value: set(),
        MeetingStatus.CANCELLED.value: set(),
    }

    def __init__(self, repo: CollaborationRepository):
        self.repo = repo

    def create_meeting(
        self,
        actor: Actor,
        title: str,
        description: str,
        agenda: str,
        scheduled_at: str,
    ) -> int:
        ensure_can_manage_meetings(actor)
        clean_title = self._require_text(title, "Meeting title is required")
        clean_scheduled_at = self._normalize_datetime(scheduled_at)
        return self.repo.create_meeting(
            title=clean_title,
            description=description.strip(),
            agenda=agenda.strip(),
            status=MeetingStatus.SCHEDULED.value,
            created_by=actor.user_id,
            scheduled_at=clean_scheduled_at,
        )

    def list_meetings(self, actor: Actor, *, status: str | None = None) -> list[dict]:
        meetings = self.repo.list_meetings()
        if status:
            status_value = MeetingStatus(status).value
            meetings = [meeting for meeting in meetings if meeting.get("status") == status_value]
        return meetings

    def get_meeting(self, actor: Actor, meeting_id: int) -> dict:
        return self.repo.get_meeting(meeting_id)

    def get_notes(self, actor: Actor, meeting_id: int) -> list[dict]:
        self.get_meeting(actor, meeting_id)
        return self.repo.list_meeting_notes(meeting_id)

    def start_meeting(self, actor: Actor, meeting_id: int) -> None:
        self._transition_meeting(actor, meeting_id, MeetingStatus.IN_PROGRESS.value, started_at=self._now())

    def complete_meeting(self, actor: Actor, meeting_id: int) -> None:
        self._transition_meeting(actor, meeting_id, MeetingStatus.COMPLETED.value, ended_at=self._now())

    def cancel_meeting(self, actor: Actor, meeting_id: int) -> None:
        self._transition_meeting(actor, meeting_id, MeetingStatus.CANCELLED.value, ended_at=self._now())

    def add_participant(self, actor: Actor, meeting_id: int, user_id: str) -> None:
        ensure_can_manage_meetings(actor)
        self.repo.add_meeting_participant(meeting_id, self._require_text(user_id, "Participant user ID is required"), actor.user_id)

    def remove_participant(self, actor: Actor, meeting_id: int, user_id: str) -> None:
        ensure_can_manage_meetings(actor)
        self.repo.remove_meeting_participant(meeting_id, self._require_text(user_id, "Participant user ID is required"))

    def list_participants(self, actor: Actor, meeting_id: int) -> list[dict]:
        self.get_meeting(actor, meeting_id)
        return self.repo.list_meeting_participants(meeting_id)

    def add_note(self, actor: Actor, meeting_id: int, content: str) -> int:
        meeting = self.get_meeting(actor, meeting_id)
        self._ensure_can_add_note(actor, meeting_id, meeting)
        return self.repo.create_meeting_note(meeting_id, actor.user_id, self._require_text(content, "Meeting note is required"))

    def edit_note(self, actor: Actor, note_id: int, content: str) -> None:
        note = self.repo.get_meeting_note(note_id)
        meeting = self.get_meeting(actor, int(note["meeting_id"]))
        if actor.role not in {Role.LEADER, Role.CO_LEAD} and actor.user_id != note["author_id"]:
            raise PermissionDeniedError("You are not allowed to edit this meeting note")
        if meeting.get("status") not in {MeetingStatus.SCHEDULED.value, MeetingStatus.IN_PROGRESS.value}:
            raise InvalidTransitionError("This meeting note can no longer be edited")
        self.repo.update_meeting_note(note_id, {"content": self._require_text(content, "Meeting note is required")})

    def _transition_meeting(self, actor: Actor, meeting_id: int, status: str, **fields: str) -> None:
        ensure_can_manage_meetings(actor)
        meeting = self.repo.get_meeting(meeting_id)
        target_status = MeetingStatus(status).value
        if target_status not in self._TRANSITIONS.get(meeting["status"], set()):
            raise InvalidTransitionError(
                f"Invalid meeting status transition: {meeting['status']} -> {target_status}"
            )
        update_fields = {"status": target_status, **fields}
        self.repo.update_meeting(meeting_id, update_fields)

    def _ensure_can_add_note(self, actor: Actor, meeting_id: int, meeting: dict) -> None:
        if actor.role in {Role.LEADER, Role.CO_LEAD}:
            return
        participants = self.repo.list_meeting_participants(meeting_id)
        if actor.user_id in {participant["user_id"] for participant in participants}:
            return
        raise PermissionDeniedError("You are not allowed to add notes to this meeting")

    def _normalize_datetime(self, value: str) -> str:
        clean_value = self._require_text(value, "Scheduled time is required")
        try:
            parsed = datetime.fromisoformat(clean_value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidInputError("Use a valid scheduled time.") from exc
        return parsed.isoformat(sep=" ", timespec="minutes")

    def _require_text(self, value: str, message: str) -> str:
        clean_value = value.strip()
        if not clean_value:
            raise InvalidInputError(message)
        return clean_value

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(sep=" ", timespec="seconds")
