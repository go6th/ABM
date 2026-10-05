"""Re-export of the run state machine, which belongs to the run lifecycle (``runs.transitions``).

Kept so existing imports work; the dependency points one way (workflow to runs), never back.
"""

from account_intelligence.runs.transitions import TERMINAL as TERMINAL
from account_intelligence.runs.transitions import TRANSITIONS as TRANSITIONS
from account_intelligence.runs.transitions import RunStatus as RunStatus
from account_intelligence.runs.transitions import transition as transition
