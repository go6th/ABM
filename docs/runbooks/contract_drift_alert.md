# Runbook: Contract Drift Alert
**Description**: A provider changed their response shape. Breaking drift disables the operation to prevent crashes or bad data.
**Action**:
1. Check `contract_drift_events` for the exact payload and severity.
2. Record a new fixture if the change is valid and expected.
3. Update the fingerprint and re-enable the operation.
