from cse_hq_bot.repositories.activity_repository import ActivityRepository


class ActivityService:
    DEFAULT_LIMIT = 20
    MAX_LIMIT = 100

    def __init__(self, repo: ActivityRepository):
        self.repo = repo

    def list_recent_activity(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict]:
        return self.repo.list(
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            limit=self._normalize_limit(limit),
        )

    def list_entity_activity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        event_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict]:
        return self.list_recent_activity(
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def list_actor_activity(
        self,
        actor_id: str,
        *,
        entity_type: str | None = None,
        event_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict]:
        return self.list_recent_activity(
            actor_id=actor_id,
            entity_type=entity_type,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def list_activity_between(
        self,
        start_time: str,
        end_time: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[dict]:
        return self.list_recent_activity(
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def _normalize_limit(self, limit: int) -> int:
        return max(1, min(int(limit), self.MAX_LIMIT))
