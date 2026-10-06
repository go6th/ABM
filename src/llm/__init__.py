"""Model routing (D-48): the only entry to a language model. Task kind -> tier -> candidate ladder
from ``config/model_routing.yaml``; fallback is selection, the node RetryPolicy is the only retry
owner; every response is schema-validated in code; every call is ledgered and replayable."""
