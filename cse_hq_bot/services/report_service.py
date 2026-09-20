from cse_hq_bot.models import Actor, Role
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.github_service import GitHubService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService


class ReportService:
    def __init__(
        self,
        project_service: ProjectService,
        task_service: TaskService,
        bug_service: BugService,
        standup_service: StandupService,
        github_service: GitHubService | None = None,
    ):
        self.project_service = project_service
        self.task_service = task_service
        self.bug_service = bug_service
        self.standup_service = standup_service
        self.github_service = github_service

    def weekly_progress_report(self) -> str:
        dashboard = self.project_service.get_dashboard()
        standups = self.standup_service.weekly_standup_summary()
        report = (
            f"Weekly Report for {dashboard.name}\n"
            f"Tasks: {dashboard.task_done}/{dashboard.task_total} completed\n"
            f"Bugs: {dashboard.bug_open}/{dashboard.bug_total} open\n"
            f"Standups: {standups['entries']} updates from {len(standups['members'])} members\n"
            f"Active blockers: {len(standups['blockers'])}"
        )
        if self.github_service is not None and self.github_service.enabled:
            system_actor = Actor("system", Role.LEADER)
            pull_requests = self.github_service.list_recent_pull_requests(system_actor)
            issues = self.github_service.list_recent_issues(system_actor)
            report += (
                "\nDevelopment\n"
                f"PRs merged: {sum(1 for item in pull_requests if item.get('merged_at'))}\n"
                f"PRs open: {sum(1 for item in pull_requests if item.get('state') == 'OPEN')}\n"
                f"Issues closed: {sum(1 for item in issues if item.get('state') == 'CLOSED')}\n"
                f"CI failures: {sum(1 for item in pull_requests if item.get('checks_status') == 'FAILING')}"
            )
        return report
