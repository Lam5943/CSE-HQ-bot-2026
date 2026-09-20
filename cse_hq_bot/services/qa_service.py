from cse_hq_bot.ai.base import AIProvider
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.repositories.task_repository import TaskRepository


class QAService:
    def __init__(
        self,
        provider: AIProvider,
        project_repo: ProjectRepository,
        task_repo: TaskRepository,
        bug_repo: BugRepository,
    ):
        self.provider = provider
        self.project_repo = project_repo
        self.task_repo = task_repo
        self.bug_repo = bug_repo

    def build_context(self) -> str:
        settings = self.project_repo.get_settings()
        tasks = self.task_repo.list_all()[:10]
        bugs = self.bug_repo.list_all()[:10]
        lines = [
            f"Project: {settings['name']}",
            f"Description: {settings['description']}",
            "Tasks:",
        ]
        lines.extend(
            f"- #{task['id']} [{task['status']}] {task['title']}"
            for task in tasks
        )
        lines.append("Bugs:")
        lines.extend(
            f"- #{bug['id']} [{bug['status']}] {bug['title']}"
            for bug in bugs
        )
        return "\n".join(lines)

    def ask(self, question: str) -> str:
        context = self.build_context()
        prompt = (
            "You are a project assistant for CSE-HQ. Use only the provided context. "
            "If context is insufficient, explicitly say you do not have enough information. "
            "Never claim to update records or perform actions.\n\n"
            f"Context:\n{context}\n\nQuestion: {question}"
        )
        return self.provider.answer(prompt)
