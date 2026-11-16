"""What a step is, before and after it happens.

The previous tool expressed a run as a generator: `yield` a step, and the
caller's only verb was "next". That is exactly right for an unattended scan and
wrong for a person sitting at the page, who wants three things a generator
cannot give them — see the prompt before paying for it, run one step again, and
change the panel after seeing how the controls went. A generator cannot be
rewound and has no seam to hand an edited prompt back through.

So a run here is **a list of executed steps, and nothing else**. Everything the
machine needs is a fold over that list: what the best designator is, which
reader qualified, what the score comes to. `plan()` is a pure function of the
history and `execute()` only ever appends to it. Three properties fall out:

  re-running a step   is truncating the history and planning again. No rollback
                      logic, because nothing was mutated in place.
  auto mode           is `while (p := plan()): execute(p)`. It is literally the
                      same traversal as manual mode rather than a second one,
                      which is the property the old generator had and the one
                      worth keeping (convention 11).
  the audit trail     is the state. A stored run is not a summary of what
                      happened; it is what happened.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Free, paid, and the one in between. `calls` on a `Planned` is the honest
#: answer to "what will this cost me", and a step with none spends nothing —
#: which is how a negative can be settled without a single paid call.
FREE: list["Call"] = []


@dataclass
class Call:
    """One model call, fully formed, before it is sent.

    This is the object the request is made from, not a description of it. A
    pre-flight assembled from a second copy of the prompt rule would describe a
    run that is not the one about to happen — the failure convention 9 records,
    where an analysis harness re-implemented a production call and 68 wrong
    verdicts reached a protocol's conclusions.
    """

    role: str                      # generation | backtranslation | judge | attest | control | convert
    model: str
    user: str
    system: str | None = None
    max_tokens: int = 400
    label: str = ""

    def as_dict(self) -> dict:
        return {"role": self.role, "model": self.model, "system": self.system,
                "user": self.user, "max_tokens": self.max_tokens,
                "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> "Call":
        return cls(role=str(d.get("role") or ""), model=str(d.get("model") or ""),
                   user=str(d.get("user") or ""),
                   system=d.get("system") or None,
                   max_tokens=int(d.get("max_tokens") or 400),
                   label=str(d.get("label") or ""))


@dataclass
class Planned:
    """The next step, worked out before anything is spent.

    `calls` is what it will send. The page shows them, lets them be rewritten
    where `editable`, and hands them back to `execute` — so what you approved is
    what goes out.
    """

    kind: str
    phase: str
    title: str
    explains: str
    summary: str
    calls: list[Call] = field(default_factory=list)
    editable: bool = True
    #: Machine-private. Carried through the page untouched so `execute` does not
    #: have to re-derive which candidate or which attestor this step is for.
    context: dict = field(default_factory=dict)

    @property
    def cost(self) -> int:
        return len(self.calls)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "phase": self.phase, "title": self.title,
                "explains": self.explains, "summary": self.summary,
                "calls": [c.as_dict() for c in self.calls], "cost": self.cost,
                "editable": self.editable, "context": self.context}


@dataclass
class Executed:
    """One step that has happened, with everything it produced.

    `obj` is the live payload — an `ItemOutcome`, a `Conversion`, a
    `Competence`. It is what the fold reads and it is deliberately *not*
    serialised: a run that outlives the process is an audit trail to read, not a
    state to resume from, and pretending otherwise would mean maintaining a
    second way of reconstructing a half-finished measurement.
    """

    ordinal: int
    kind: str
    phase: str
    title: str
    explains: str
    summary: str
    tone: str = ""
    status: str = "done"             # done | failed
    request: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    calls: dict = field(default_factory=dict)
    edited: bool = False
    attempt: int = 0
    obj: Any = None

    def as_dict(self) -> dict:
        return {"ordinal": self.ordinal, "attempt": self.attempt, "kind": self.kind,
                "phase": self.phase, "title": self.title, "explains": self.explains,
                "summary": self.summary, "tone": self.tone, "status": self.status,
                "request": self.request, "result": self.result,
                "calls": dict(self.calls), "edited": self.edited}

    def row(self) -> dict:
        """The persistable shape. `obj` is not in it, by design."""
        return {"ordinal": self.ordinal, "attempt": self.attempt, "kind": self.kind,
                "phase": self.phase, "status": self.status, "tone": self.tone,
                "summary": self.summary, "request": self.request or None,
                "result": self.result or None, "calls": dict(self.calls) or None,
                "edited": self.edited}


def tone_for(ok: bool | None) -> str:
    """Three states, because an abstention is not a failure.

    The distinction is the method's, not the page's. A gate verdict that voids
    an item — memorised text, too short, a language-ID call too weak to commit
    on — is neither a pass nor evidence against the model, and colouring it like
    a failure would say the opposite of what the arithmetic does with it.
    """
    return "ok" if ok is True else "warn" if ok is None else "bad"
