class TenantAccessError(PermissionError):
    """Raised when a caller tries to access data outside its tenant."""


class NotFoundError(LookupError):
    """Raised when a requested entity does not exist."""


class RunTransitionError(ValueError):
    """Raised when a run status transition is not allowed."""


class AgentActionLeaseConflictError(RuntimeError):
    """Raised when an action observation is committed without a valid lease fence."""
