from datetime import datetime

from cse_hq_bot.models import Actor, ProjectDashboard
from cse_hq_bot.permissions import ensure_can_manage_project
from cse_hq_bot.repositories.project_repository import ProjectRepository


class ProjectService:
    def __init__(self, repo: ProjectRepository):
        self.repo = repo

    def get_dashboard(self) -> ProjectDashboard:
        settings = self.repo.get_settings()
        counts = self.repo.get_summary_counts()
        return ProjectDashboard(
            name=settings["name"],
            description=settings["description"],
            updated_at=datetime.fromisoformat(settings["updated_at"].replace(" ", "T")),
            **counts,
        )

    def update_settings(self, actor: Actor, name: str, description: str) -> None:
        ensure_can_manage_project(actor)
        self.repo.update_settings(name=name, description=description)
