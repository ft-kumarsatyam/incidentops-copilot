"""Domain errors, mapped to HTTP responses by the API layer."""


class IncidentNotFoundError(LookupError):
    """No incident exists for the given run id."""


class IncidentConflictError(RuntimeError):
    """The requested transition is not valid for the incident's current state."""
