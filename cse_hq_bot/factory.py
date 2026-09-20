from cse_hq_bot.ai.fake_provider import FakeAIProvider
from cse_hq_bot.ai.gemini_provider import GeminiProvider
from cse_hq_bot.config import Config
from cse_hq_bot.db import Database
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.activity_service import ActivityService
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.collab_service import CollaborationService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.ai_service import AIService
from cse_hq_bot.services.ai_session_service import AISessionService
from cse_hq_bot.services.project_context_service import ProjectContextService
from cse_hq_bot.services.prompt_builder import PromptBuilder
from cse_hq_bot.services.qa_service import QAService
from cse_hq_bot.services.retrieval_planner import RetrievalPlanner
from cse_hq_bot.services.report_service import ReportService
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService


class ServiceContainer:
    def __init__(self, config: Config):
        db = Database(config.database_path)
        db.initialize()

        project_repo = ProjectRepository(db)
        ai_session_repo = AISessionRepository(db)
        task_repo = TaskRepository(db)
        bug_repo = BugRepository(db)
        collab_repo = CollaborationRepository(db)
        activity_repo = ActivityRepository(db)

        self.project_service = ProjectService(project_repo)
        self.activity_service = ActivityService(activity_repo)
        self.task_service = TaskService(task_repo, activity_repo)
        self.bug_service = BugService(bug_repo, activity_repo)
        self.collab_service = CollaborationService(collab_repo, activity_repo)
        self.meeting_service = MeetingService(collab_repo, activity_repo)
        self.decision_service = DecisionService(collab_repo, activity_repo)
        self.standup_service = StandupService(collab_repo, activity_repo)
        self.project_context_service = ProjectContextService(
            self.project_service,
            self.task_service,
            self.bug_service,
            self.meeting_service,
            self.decision_service,
            self.standup_service,
            self.activity_service,
        )
        self.report_service = ReportService(
            self.project_service,
            self.task_service,
            self.bug_service,
            self.standup_service,
        )

        provider = (
            GeminiProvider(config.gemini_api_key, config.ai_model)
            if config.ai_provider == "gemini"
            else FakeAIProvider()
        )
        self.retrieval_planner = RetrievalPlanner()
        self.prompt_builder = PromptBuilder()
        self.ai_service = AIService(
            provider,
            self.project_context_service,
            self.retrieval_planner,
            self.prompt_builder,
            max_context_items=config.ai_max_context_items,
            request_timeout=config.ai_request_timeout,
        )
        self.ai_session_service = AISessionService(
            ai_session_repo,
            self.ai_service,
            max_history_messages=config.ai_max_history_messages,
        )
        self.qa_service = QAService(self.ai_service)
