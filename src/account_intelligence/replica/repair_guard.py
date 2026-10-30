"""The client-file rules of the application, handed to the workspace repair stage.

``exports.repair.package`` must not import ``replica`` or ``agents`` (the import contract: exports
sits below both), so it works through a ``ClientGuard`` it is given. This module builds that guard
from the rules every client file already passes (``replica.sheets``: format rules, tool names,
provider hosts, the finished-file check) and installs it; the Download everything build calls
``install`` before it packages anything."""

from __future__ import annotations

from account_intelligence.replica import sheets as SH
from agents.naming import third_party_domain_hits, tool_name_hits
from exports.repair import package as PK


def build() -> PK.ClientGuard:
    """The guard made of the live client-file rules."""

    def text_trips(text: str) -> bool:
        return bool(
            SH._GATE_DASHES.search(text) or third_party_domain_hits(text) or tool_name_hits(text)
        )

    return PK.ClientGuard(
        safe_cell=SH.safe_cell,
        check_finished=SH.check_finished,
        text_trips=text_trips,
        gate_dashes=SH._GATE_DASHES,
        withheld=SH.WITHHELD,
        blocked=SH.ExportBlocked,
    )


def install() -> None:
    """Install the live guard in the package writer."""
    PK.install_guard(build())
