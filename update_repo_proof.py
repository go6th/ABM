import re

with open("src/account_intelligence/tracker/repo.py", "r") as f:
    code = f.read()

code = code.replace("SELECT priority, status, reason_code, proof_hash, swept, attempts, version", "SELECT priority, status, reason_code, proof, proof_hash, swept, attempts, version")

def_replacement = """
        status = Status(row.status) if row.status else None
        reason = ReasonCode(row.reason_code) if row.reason_code else None
        proof = Proof.model_validate_json(row.proof) if row.proof else None
        
        state = TrackerRowState(
            key=row_key,
            priority=row.priority,
            status=status,
            reason_code=reason,
            proof_hash=row.proof_hash,
            swept=row.swept,
            attempts=row.attempts
        )
        return state, version, proof
"""
# Hand patching repo.py
