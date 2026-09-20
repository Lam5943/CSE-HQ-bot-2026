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


class AIMalformedResponseError(AIProviderError):
    """Raised when the AI provider returns an unusable response."""


class AISessionClosedError(CSEHQError):
    """Raised when an AI session is closed."""


class AISessionBusyError(CSEHQError):
    """Raised when an AI session already has an in-flight request."""


class AISessionConflictError(CSEHQError):
    """Raised when a Discord thread is already bound to an AI session."""
