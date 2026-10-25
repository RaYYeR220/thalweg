"""Named failures. Lambda reports the exception class name as the Step Functions error,
so the state machine can retry or route on it (see statemachine/gauging.asl.json)."""


class BadInput(Exception):
    """The execution input is missing fields or has invalid values."""


class StationNotFound(Exception):
    """No item for the station in thalweg-stations."""


class ClipNotReady(Exception):
    """The clip or its sidecar is not in the ingest bucket (yet). Retried by the state machine."""


class BadSidecar(Exception):
    """The sidecar JSON is unreadable or inconsistent with the run."""


class AgentOutputInvalid(Exception):
    """The worker's result does not satisfy the decision contract. The run fails closed."""
