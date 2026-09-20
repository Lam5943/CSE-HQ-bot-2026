class CSEHQError(Exception):
    """Base domain error for CSE-HQ bot."""


class PermissionDeniedError(CSEHQError):
    """Raised when a user does not have permission for an operation."""


class NotFoundError(CSEHQError):
    """Raised when a requested resource does not exist."""


class AIProviderError(CSEHQError):
    """Raised when AI provider operations fail."""
