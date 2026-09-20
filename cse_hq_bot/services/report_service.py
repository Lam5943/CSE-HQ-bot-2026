from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.collab_service import CollaborationService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.task_service import TaskService


class ReportService:
    def __init__(
        self,
        project_service: ProjectService,
        task_service: TaskService,
        bug_service: BugService,
        collab_service: CollaborationService,
    ):
        self.project_service = project_service
        self.task_service = task_service
        self.bug_service = bug_service
        self.collab_service = collab_service

    def weekly_progress_report(self) -> str:
        dashboard = self.project_service.get_dashboard()
        standups = self.collab_service.weekly_standup_summary()
        return (
            f"Weekly Report for {dashboard.name}\n"
            f"Tasks: {dashboard.task_done}/{dashboard.task_total} completed\n"
            f"Bugs: {dashboard.bug_open}/{dashboard.bug_total} open\n"
            f"Standups: {standups['entries']} updates from {len(standups['members'])} members\n"
            f"Active blockers: {len(standups['blockers'])}"
        )
