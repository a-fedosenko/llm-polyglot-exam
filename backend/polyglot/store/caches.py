"""Database-backed competence caches.

Both classes deliberately expose the *same surface* as their in-memory
counterparts and nothing more. That is not tidiness: the invariant that a
record routes but never vouches has to survive persistence, and the way to be
sure of it is for the persistent version to be unable to express anything the
in-memory one cannot. There is no `is_trusted()` here because the moment one
existed somebody would call it to skip a control, and a wrongly-recorded
attestor would become permanently self-confirming.
"""
from __future__ import annotations

from polyglot.core.bt.qualify import Qualification, QualStatus
from polyglot.settings import METHOD_VERSION
from polyglot.store import session
from polyglot.store import repo


class DbQualificationCache:
    """Whether a reader can be trusted for a language, remembered across runs.

    Qualifying a back-translator costs four chrF++ calls, and the answer changes
    only when the reader does. Caching it is what makes a second run of the same
    language cheap; it is never consulted to skip a check the method requires.
    """

    def __init__(self, method_version: str = METHOD_VERSION) -> None:
        self.method_version = method_version

    def get(self, bt_id: str, tag: str) -> Qualification | None:
        with session() as s:
            row = repo.get_qualification(s, bt_id, tag, self.method_version)
            if row is None:
                return None
            return Qualification(QualStatus(row.status), row.score, bt_id, tag,
                                 row.kind or "", row.detail or "")

    def put(self, q: Qualification) -> None:
        with session() as s:
            repo.put_qualification(s, {
                "backtranslator": q.backtranslator, "tag": q.tag,
                "status": q.status.value, "score": q.score,
                "kind": q.kind or None, "detail": q.detail or None,
                "method_version": self.method_version})


class DbAttestorRecord:
    """`panel.CapabilityRecord`, backed by the database.

    Append-only with a current flag, like every other verdict here: a re-run
    adds a row and promotion is a reversible pointer move. Controls still run on
    every single run regardless of what is stored, which is exactly why a stale
    row costs wasted routing and never a wrong verdict.
    """

    def __init__(self, method_version: str = METHOD_VERSION) -> None:
        self.method_version = method_version

    def put(self, cap, *, promote: bool | None = None):
        with session() as s:
            repo.put_attestor(s, {
                "model": cap.key.model, "tag": cap.key.tag,
                "role": cap.key.role.value, "script": cap.key.script,
                "qualified": cap.qualified, "confidence": cap.confidence,
                "probes": list(cap.probes) or None, "detail": cap.detail or None,
                "evidence": getattr(cap, "evidence", None) or None,
                "method_version": self.method_version,
            }, promote=promote)
        return cap

    def get(self, key):
        from polyglot.core.probe.panel import Capability

        with session() as s:
            row = repo.get_attestor(s, key.model, key.tag, key.role.value,
                                    self.method_version, key.script)
            if row is None:
                return None
            cap = Capability(key=key, qualified=row.qualified,
                             confidence=row.confidence, probes=row.probes or [],
                             detail=row.detail or "",
                             measured_at=row.measured_at.timestamp())
            cap.row_id, cap.current = row.id, row.current
            cap.evidence = row.evidence
            return cap

    def known(self, attestors: list[str], *, tag: str, role,
              script: str | None = None) -> dict[str, bool]:
        """Routing hints only: attestor -> was-qualified-before.

        Absent means never measured, which is ordered differently from
        measured-and-failed — an untried attestor is worth asking before one
        that failed, and both are still asked.
        """
        with session() as s:
            rows = {r.model: r.qualified for r in repo.attestors_for(
                s, tag, role.value, self.method_version, script)}
        return {a: rows[a] for a in attestors if a in rows}

    def rows(self) -> list:
        from sqlalchemy import select

        from polyglot.core.probe.panel import Capability, CapabilityKey, Role
        from polyglot.store.models import AttestorRecord

        with session() as s:
            out = []
            for r in s.scalars(select(AttestorRecord).where(
                    AttestorRecord.method_version == self.method_version,
                    AttestorRecord.current.is_(True))):
                out.append(Capability(
                    key=CapabilityKey(r.model, r.tag, Role(r.role), r.script),
                    qualified=r.qualified, confidence=r.confidence,
                    probes=r.probes or [], detail=r.detail or "",
                    measured_at=r.measured_at.timestamp()))
            return out
