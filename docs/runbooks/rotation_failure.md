# Runbook: Rotation Failure / Overdue Alert
**Description**: A secret rotation is overdue according to `config/secrets_rotation.yaml`.
**Action**:
1. Generate the new secret.
2. Store it as the secondary key.
3. Wait for the overlap window.
4. Promote to primary.
