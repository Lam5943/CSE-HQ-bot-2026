from cse_hq_bot.ai.fake_provider import FakeAIProvider
from cse_hq_bot.ai.gemini_provider import GeminiProvider
from cse_hq_bot.ai.openai_provider import OpenAIProvider
from cse_hq_bot.ai.provider_router import AIProviderRouter
from cse_hq_bot.config import Config
from cse_hq_bot.db import Database
from cse_hq_bot.errors import AIConfigurationError
from cse_hq_bot.github_provider import GitHubProvider
from cse_hq_bot.github_webhook import (
    GitHubWebhookProcessor,
    GitHubWebhookServer,
)
from cse_hq_bot.repositories.activity_repository import ActivityRepository
from cse_hq_bot.repositories.ai_action_repository import AIActionProposalRepository
from cse_hq_bot.repositories.ai_session_repository import AISessionRepository
from cse_hq_bot.repositories.bug_repository import BugRepository
from cse_hq_bot.repositories.collab_repository import CollaborationRepository
from cse_hq_bot.repositories.forum_repository import ForumRepository
from cse_hq_bot.repositories.github_repository import GitHubRepositoryCache
from cse_hq_bot.repositories.project_repository import ProjectRepository
from cse_hq_bot.repositories.task_repository import TaskRepository
from cse_hq_bot.services.activity_service import ActivityService
from cse_hq_bot.services.ai_action_interpreter import AIActionInterpreter
from cse_hq_bot.services.ai_action_registry import AIActionRegistry
from cse_hq_bot.services.ai_action_service import AIActionService
from cse_hq_bot.services.ai_service import AIService
from cse_hq_bot.services.ai_session_service import AISessionService
from cse_hq_bot.services.bug_service import BugService
from cse_hq_bot.services.collab_service import CollaborationService
from cse_hq_bot.services.decision_service import DecisionService
from cse_hq_bot.services.forum_publishing_service import ForumPublishingService
from cse_hq_bot.services.github_event_service import GitHubEventService
from cse_hq_bot.services.github_service import GitHubService
from cse_hq_bot.services.health_service import HealthService
from cse_hq_bot.services.meeting_service import MeetingService
from cse_hq_bot.services.project_context_service import ProjectContextService
from cse_hq_bot.services.project_service import ProjectService
from cse_hq_bot.services.prompt_builder import PromptBuilder
from cse_hq_bot.services.qa_service import QAService
from cse_hq_bot.services.report_service import ReportService
from cse_hq_bot.services.retrieval_planner import RetrievalPlanner
from cse_hq_bot.services.standup_service import StandupService
from cse_hq_bot.services.task_service import TaskService


class ServiceContainer:
    def __init__(self, config: Config):
        db = Database(config.database_path)
        db.initialize()
        self.config = config
        self.db = db

        project_repo = ProjectRepository(db)
        ai_session_repo = AISessionRepository(db)
        ai_action_repo = AIActionProposalRepository(db)
        task_repo = TaskRepository(db)
        bug_repo = BugRepository(db)
        collab_repo = CollaborationRepository(db)
        activity_repo = ActivityRepository(db)
        github_repo = GitHubRepositoryCache(db)
        forum_repo = ForumRepository(db)

        self.project_service = ProjectService(project_repo)
        self.activity_service = ActivityService(activity_repo)
        self.task_service = TaskService(task_repo, activity_repo)
        self.forum_publishing_service = ForumPublishingService(forum_repo)
        self.bug_service = BugService(
            bug_repo,
            activity_repo,
            publication_hook=self.forum_publishing_service.publish_bug_nowait,
        )
        self.collab_service = CollaborationService(collab_repo, activity_repo)
        self.meeting_service = MeetingService(collab_repo, activity_repo)
        self.decision_service = DecisionService(collab_repo, activity_repo)
        self.standup_service = StandupService(collab_repo, activity_repo)
        github_provider = (
            GitHubProvider(
                config.github_repository_owner,
                config.github_repository_name,
                config.github_token,
                timeout_seconds=config.github_request_timeout,
                max_results=config.github_max_results,
            )
            if config.github_enabled
            else None
        )
        self.github_service = GitHubService(
            github_repo,
            github_provider,
            self.task_service,
            self.bug_service,
            enabled=config.github_enabled,
            max_results=config.github_max_results,
            cache_ttl=config.github_cache_ttl,
        )
        self.github_event_service = GitHubEventService(
            self.forum_publishing_service,
            bug_label=config.github_bug_label,
        )
        self.github_webhook_processor = None
        self.github_webhook_server = None
        if config.github_webhook_enabled:
            repository_full_name = (
                f"{config.github_repository_owner}/{config.github_repository_name}"
                if config.github_repository_owner and config.github_repository_name
                else ""
            )
            self.github_webhook_processor = GitHubWebhookProcessor(
                forum_repo,
                self.github_event_service,
                secret=config.github_webhook_secret or "",
                repository_full_name=repository_full_name,
            )
            self.github_webhook_server = GitHubWebhookServer(
                self.github_webhook_processor,
                host=config.webhook_host,
                port=config.webhook_port,
                path=config.github_webhook_path,
            )
        self.project_context_service = ProjectContextService(
            self.project_service,
            self.task_service,
            self.bug_service,
            self.meeting_service,
            self.decision_service,
            self.standup_service,
            self.activity_service,
            self.github_service,
        )
        self.report_service = ReportService(
            self.project_service,
            self.task_service,
            self.bug_service,
            self.standup_service,
            self.github_service,
        )

        provider = build_ai_provider(config)
        self.ai_provider = provider
        self.health_service = HealthService(
            config,
            db,
            self.github_service,
            forum_repo,
            webhook_server=self.github_webhook_server,
            ai_provider=provider,
        )
        self.ai_action_registry = AIActionRegistry(
            self.task_service,
            self.bug_service,
            self.meeting_service,
            self.decision_service,
            self.standup_service,
        )
        self.ai_action_service = AIActionService(
            ai_action_repo,
            ai_session_repo,
            self.ai_action_registry,
            expiration_seconds=config.ai_action_expiration_seconds,
        )
        self.ai_action_interpreter = AIActionInterpreter(
            provider,
            self.ai_action_registry,
            self.task_service,
            self.bug_service,
            self.meeting_service,
            self.decision_service,
            self.standup_service,
            timeout_seconds=config.ai_request_timeout,
            max_candidates=config.ai_max_context_items,
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
            action_interpreter=self.ai_action_interpreter,
        )
        self.ai_session_service = AISessionService(
            ai_session_repo,
            self.ai_service,
            max_history_messages=config.ai_max_history_messages,
            action_service=self.ai_action_service,
        )
        self.qa_service = QAService(self.ai_service)


def build_ai_provider(config: Config):
    primary = (
        GeminiProvider(config.gemini_api_key, config.ai_model)
        if config.ai_provider == "gemini"
        else FakeAIProvider()
    )
    if not config.ai_fallback_enabled:
        return primary
    if config.ai_fallback_provider != "openai":
        raise AIConfigurationError(
            f"Unsupported AI fallback provider: {config.ai_fallback_provider}"
        )

    fallback = None
    fallback_configuration_error = None
    try:
        fallback = OpenAIProvider(config.openai_api_key, config.openai_model)
    except AIConfigurationError as exc:
        fallback_configuration_error = exc
    return AIProviderRouter(
        primary,
        fallback,
        fallback_enabled=True,
        primary_name=config.ai_provider,
        fallback_name=config.ai_fallback_provider,
        fallback_configuration_error=fallback_configuration_error,
    )
