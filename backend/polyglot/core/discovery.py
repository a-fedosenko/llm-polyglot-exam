"""What models this installation can actually reach.

There are two configurations and they answer this question with different
authority, which the caller has to know about:

    a custom model list   **binding.** The list *is* the configuration: a model
                          absent from it has no endpoint and no key, so naming
                          one is refused before anything is spent.
    a gateway             **advisory.** It is the gateway's opinion about what
                          it serves. A name missing from it is still worth
                          trying, and an unreachable gateway must degrade the
                          picker to a free-text field rather than break the page.

Discovery exists because the alternative is typing a model id from memory, and
that is how a budget gets spent on a typo. It is not a quality judgement and
makes no claim that a listed model is any good at anything — that is what the
reader qualification and the attestor controls are for, and they run regardless
of what is listed here.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from polyglot.core.families import model_family
from polyglot.settings import settings

#: How long a gateway's list is trusted before it is fetched again. A custom
#: list is a local file and is re-read every call instead: a picker that needs a
#: restart to notice a model you just added is the kind of staleness this
#: project has been bitten by before.
TTL_S = 300

#: Ids that are not chat models and cannot play any role here. A name heuristic,
#: and knowingly a weak one — but offering an embedding model as a judge means
#: finding out through a failed run, and a gateway's list carries no capability
#: flags to filter on properly.
NOT_CHAT = ("embedding", "embed-", "-embed", "rerank", "whisper", "tts-",
            "dall-e", "moderation")

_cache: tuple[float, list[str]] | None = None


@dataclass(frozen=True)
class Model:
    id: str
    title: str = ""
    endpoint: str = ""

    @property
    def family(self) -> str:
        return model_family(self.id)


def _is_chat(model_id: str) -> bool:
    low = model_id.lower()
    return not any(x in low for x in NOT_CHAT)


def available() -> tuple[list[Model], bool]:
    """(models, binding). `binding` says whether the list is the whole truth."""
    from polyglot.core.client import NotConfigured, custom_models

    try:
        entries = custom_models()
    except NotConfigured:
        # A broken custom list is reported where a run is attempted, with the
        # parse error. Here it only means the picker has nothing to offer.
        entries = []
    if entries:
        return ([Model(e.model_id, e.title,
                       e.v1_endpoint.split("//")[-1].split("/")[0])
                 for e in entries if _is_chat(e.model_id)], True)
    return ([Model(i, i) for i in _gateway_models()], False)


def _gateway_models() -> list[str]:
    """Model ids from the gateway, cached for `TTL_S`.

    Failure is silent and returns nothing, by design: an unreachable gateway
    degrades the picker to a free-text field. Breaking the page because a list
    endpoint is down would be a worse answer than letting someone type an id.
    """
    global _cache
    if _cache and time.monotonic() - _cache[0] < TTL_S:
        return _cache[1]
    if not settings.aggregator_base_url:
        return []

    url = (settings.aggregator_model_list_url
           or settings.aggregator_base_url.rstrip("/") + "/models")
    req = urllib.request.Request(url)
    if settings.aggregator_admin_api_key:
        req.add_header("Authorization", f"Bearer {settings.aggregator_admin_api_key}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.load(r)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []

    rows = body.get("data", body) if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return []
    ids = sorted({str(x["id"]) for x in rows
                  if isinstance(x, dict) and x.get("id") and _is_chat(str(x["id"]))})
    _cache = (time.monotonic(), ids)
    return ids


def refresh() -> None:
    global _cache
    _cache = None


def propose(engine: str, models: list[Model]) -> dict:
    """A starting judge and reader panel, and the rule that produced them.

    **Not a recommendation, and it says so.** Which model reads a language well
    is exactly what this tool exists to find out, and nothing here knows it. The
    rule is deterministic and dull on purpose — pick from families other than
    the engine's, in listed order — so that a proposal is never mistaken for a
    measured claim about quality.

    The one thing it *does* enforce is the rule that matters: a model never
    grades or reads back its own family's output. That is not a preference and
    not a default; it is the difference between measuring a model and measuring
    its self-consistency.
    """
    fam = model_family(engine) if engine else ""
    eligible = [m for m in models if m.id != engine and m.family != fam]

    judge = eligible[0].id if eligible else None
    judge_fam = model_family(judge) if judge else ""

    # Readers from a third family where one exists, so the judge and the reader
    # are not two members of one house. They fail differently — a reader that
    # cannot read a language invents, a judge that cannot grade is lenient — and
    # two models from one family are the likeliest pair to fail the same way at
    # the same time, which would read as agreement.
    others = [m.id for m in eligible if m.family not in (judge_fam,)]
    same_house = [m.id for m in eligible if m.id != judge and m.family == judge_fam]
    readers = (others + same_house)[:2] or ([judge] if judge else [])

    return {
        "judge": judge,
        "backtranslator_panel": readers,
        "attestors": [m.id for m in eligible],
        "rule": (f"Models outside {fam or 'the engine'}'s own family, in listed "
                 f"order. A proposal, not a recommendation — which model reads a "
                 f"language well is what this tool measures, and nothing here "
                 f"knows it yet."),
        "excluded_by_family_guard": [m.id for m in models
                                     if m.id == engine or m.family == fam],
    }
