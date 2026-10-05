# Mutation check: credit cap

Tool: mutmut 3 (`uv run --with "mutmut>=3.2,<4" mutmut run`, configured in `pyproject.toml`) on
`src/spend/money.py`, `gate.py`, `priority.py`, `approval.py`, `report.py`, `reconcile.py`,
against the pure tests `tests/spend/test_core.py`, `test_properties.py` and
`test_mutation_kills.py`. Run on 2026-10-04 in a scratch copy of the repository.

| Run | Killed | Survived | No test in the selection | What changed |
| --- | --- | --- | --- | --- |
| 1 | 443 | 175 | 157 | first run |
| 2 | 583 | 35 | 157 | exact gate decisions, approval boundaries and messages, money edges, a hand-written report golden |
| 3 | 590 | 28 | 157 | extension ttl, cap equal to spend, measured replays and credits, the LLM line |
| 4 | 599 | 28 | 148 | a pure test of `reconcile.stale` |

The 148 mutants without a test are in `reconcile.reconcile_run`, which issues SQL; it is covered
(100% of its lines) by the Postgres tests (`test_db.py`, `test_edges.py`, `test_kill_resume.py`),
which are not run per mutant.

The 28 survivors, reviewed (none changes behaviour a test can observe):

* 23 wrap a refusal message in `XX...XX`: the assertion matches the message as a substring, so
  the type and the meaning are asserted.
* 2 in `format_usd`: `partition` to `rpartition` (the text has one dot) and a strip set with `X`
  added (a formatted number never contains `X`).
* `report._line`: `advisory > 0` to `>= 0` (at 0 both branches give the same flag).
* `build_report`: the threshold passed for category and operation lines (unused: they carry no
  advisory figure).

Survivor list (mutant :: changed line):

```
spend.money.x__decimal__mutmut_2 :: +        raise MoneyError("XXfloat is not moneyXX")
spend.money.x__decimal__mutmut_14 :: +        raise MoneyError("XXmoney must be finiteXX")
spend.money.x_usd_to_micros__mutmut_9 :: +        raise MoneyError("XXa cost cannot be negativeXX")
spend.money.x_micros_to_usd__mutmut_4 :: +        raise MoneyError("XXmicros must be an intXX")
spend.money.x_round_up_to_step__mutmut_4 :: +        raise MoneyError("XXstep must be positiveXX")
spend.money.x_round_up_to_step__mutmut_9 :: +        raise MoneyError("XXamount must not be negativeXX")
spend.money.x_total__mutmut_6 :: +            raise MoneyError("XXparts must be int microsXX")
spend.money.x_credits__mutmut_6 :: +        raise MoneyError("XXcredits cannot be negativeXX")
spend.money.x_format_usd__mutmut_6 :: +    text = f"{value:.6f}".rstrip("XX0XX")
spend.money.x_format_usd__mutmut_9 :: +    whole, _, frac = text.rpartition(".")
spend.gate.x_decide__mutmut_4 :: +        raise ValueError("XXan estimate cannot be negativeXX")
spend.priority.x_fund__mutmut_5 :: +        raise ValueError("XXa cost cannot be negativeXX")
spend.priority.x_fund_indexed__mutmut_5 :: +        raise ValueError("XXa cost cannot be negativeXX")
spend.approval.x_check_principal__mutmut_3 :: +        raise ApprovalInvalid("XXprincipal belongs to another tenantXX")
spend.approval.x_check_principal__mutmut_9 :: +        raise ApprovalInvalid("XXan agent cannot approve or extend a capXX")
spend.approval.x_check_principal__mutmut_16 :: +            raise ApprovalInvalid("XXrole may not approve a capXX")
spend.approval.x_check_principal__mutmut_23 :: +        raise ApprovalInvalid("XXtenant policy may not approve this capXX")
spend.approval.x_check_principal__mutmut_29 :: +        raise ApprovalInvalid("XXcap is above the tenant policy limitXX")
spend.approval.x_check_principal__mutmut_32 :: +        raise ApprovalInvalid("XXa proposal with excluded providers needs a person to approve itXX")
spend.approval.x_check_binding__mutmut_11 :: +        raise ProposalStale("XXthe proposal changed since it was shownXX")
spend.approval.x_check_binding__mutmut_18 :: +        raise ApprovalInvalid("XXthis cap version was already decidedXX")
spend.approval.x_check_binding__mutmut_27 :: +        raise ProposalStale("XXthe proposal expiredXX")
spend.approval.x_check_binding__mutmut_35 :: +        raise ProposalStale("XXthe balance snapshot is stale: propose againXX")
spend.approval.x_check_binding__mutmut_39 :: +        raise ApprovalInvalid("XXa cap cannot be lower than the spend already madeXX")
spend.approval.x_check_binding__mutmut_46 :: +        raise ApprovalInvalid("XXan extension must be strictly higher than the current capXX")
spend.report.x__line__mutmut_23 :: +        if advisory >= 0:
spend.report.x_build_report__mutmut_71 :: +            _line(line, ledger, None, None) for line in rollup.by_category
spend.report.x_build_report__mutmut_80 :: +            _line(line, ledger, None, None) for line in rollup.by_operation
```
