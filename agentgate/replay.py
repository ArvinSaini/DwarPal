"""Offline replay of every recorded gate decision.

``verify`` proves the ledger's *record* integrity (nothing was edited). ``replay`` proves its *reasoning*
integrity: each ``gate.decision`` and ``refund.decision`` event carries the exact input the pure gate consumed,
so an auditor can re-run the gate and confirm the recorded verdict, rule and every check without trusting the
process that wrote the ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agentgate.gate import GateAgent, GateInput, GateMandate, RefundInput, evaluate, evaluate_refund
from agentgate.ledger import Ledger

COMPARED = ("verdict", "rule_id", "reason", "checks", "total_paise")


@dataclass
class ReplayReport:
    decisions: int = 0
    identical: int = 0
    skipped: int = 0
    divergences: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.divergences


def gate_input_from(inp: dict) -> GateInput:
    agent = GateAgent(**inp["agent"]) if inp.get("agent") else None
    mandate = None
    if inp.get("mandate"):
        m = dict(inp["mandate"])
        m["categories"] = tuple(m.get("categories") or ())
        mandate = GateMandate(**m)
    return GateInput(agent, mandate, inp["policy"], inp["catalog"], inp["items"], inp["spent_today_paise"],
                     inp["spent_total_paise"], inp["now"], inp.get("session_status"), inp["mode"],
                     bool(inp.get("merchant_approved", False)))


def refund_input_from(inp: dict) -> RefundInput:
    r = dict(inp)
    r["seen_references"] = tuple(r.get("seen_references") or ())
    return RefundInput(**r)


def replay(ledger: Ledger) -> ReplayReport:
    report = ReplayReport()
    for e in ledger.events():
        if e.type not in ("gate.decision", "refund.decision"):
            continue
        inp = e.payload.get("input")
        if not inp:
            report.skipped += 1
            continue
        report.decisions += 1
        try:
            if e.type == "gate.decision":
                fresh = evaluate(gate_input_from(inp)).to_dict()
            else:
                fresh = evaluate_refund(refund_input_from(inp)).to_dict()
        except Exception as exc:  # a malformed recorded input is itself a divergence
            report.divergences.append({"seq": e.seq, "type": e.type, "field": "input", "recorded": "see event",
                                       "replayed": f"{type(exc).__name__}: {exc}"})
            continue
        diverged = False
        for key in COMPARED:
            if fresh.get(key) != e.payload.get(key):
                report.divergences.append({"seq": e.seq, "type": e.type, "field": key,
                                           "recorded": e.payload.get(key), "replayed": fresh.get(key)})
                diverged = True
                break
        if not diverged:
            report.identical += 1
    return report


def render_report(report: ReplayReport) -> str:
    lines = [f"{report.decisions} decision(s) replayed, {report.identical} identical, {report.skipped} skipped "
             f"(no recorded input)"]
    for d in report.divergences[:20]:
        lines.append(f"  seq {d['seq']} {d['type']}: {d['field']} recorded {str(d['recorded'])[:80]!r} "
                     f"but replay gives {str(d['replayed'])[:80]!r}")
    if len(report.divergences) > 20:
        lines.append(f"  ... and {len(report.divergences) - 20} more")
    return "\n".join(lines)
