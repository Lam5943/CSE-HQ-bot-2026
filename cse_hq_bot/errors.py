class CSEHQError(Exception):
    """Base domain error for CSE-HQ bot."""


class PermissionDeniedError(CSEHQError):
    """Raised when a user does not have permission for an operation."""


class NotFoundError(CSEHQError):
    """Raised when a requested resource does not exist."""


class InvalidTransitionError(CSEHQError):
    """Raised when a workflow status transition is invalid."""


class InvalidInputError(CSEHQError):
    """Raised when a request contains invalid domain input."""


class AIProviderError(CSEHQError):
    """Raised when AI provider operations fail."""


class AIConfigurationError(AIProviderError):
    """Raised when AI provider configuration is invalid."""


class AIRateLimitError(AIProviderError):
    """Raised when the AI provider rejects a request due to rate limits."""


class AITimeoutError(AIProviderError):
    """Raised when the AI provider times out."""


class AIProviderUnavailableError(AIProviderError):
    """Raised when an AI provider is temporarily unavailable."""


class AIMalformedResponseError(AIProviderError):
    """Raised when the AI provider returns an unusable response."""


class AISessionClosedError(CSEHQError):
    """Raised when an AI session is closed."""


class AISessionBusyError(CSEHQError):
    """Raised when an AI session already has an in-flight request."""


class AISessionConflictError(CSEHQError):
    """Raised when a Discord thread is already bound to an AI session."""


class GitHubProviderError(CSEHQError):
    """Raised when GitHub provider operations fail."""


class GitHubConfigurationError(GitHubProviderError):
    """Raised when GitHub integration configuration is invalid."""


class GitHubAuthenticationError(GitHubProviderError):
    """Raised when GitHub rejects configured credentials."""


class GitHubRateLimitError(GitHubProviderError):
    """Raised when GitHub API rate limits are exhausted."""


class GitHubTimeoutError(GitHubProviderError):
    """Raised when a GitHub API request times out."""


class GitHubNotFoundError(GitHubProviderError):
    """Raised when a requested GitHub resource does not exist."""
