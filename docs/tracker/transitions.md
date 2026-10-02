# Tracker Transition Table

| From \ To | done | partial | not_done | blocked | none |
| --- | --- | --- | --- | --- | --- |
| unset | stage, verifier | stage, verifier | stage, finalizer | stage | stage, verifier |
| not_done | new_proof | new_proof | unchanged_only | new_proof_with_failed_call | new_proof |
| blocked | new_proof_successful_call | new_proof_successful_call | no | new_proof | new_proof_successful_call |
| partial | new_proof | extend_proof | judge_or_gate_downgrade_only | no | no |
| none | new_proof_with_results | new_proof_with_results | no | no | extend_proof |
| done | extend_proof | judge_or_gate_downgrade | judge_or_gate_downgrade | no | no |
