"""Which models count as the same reader.

Separated from everything else because exactly one rule depends on it and that
rule is load-bearing: **a model never attests its own output** (protocol 005,
and the S8 panel guard). Two ids from one vendor are near enough to the same
reader that the guard has to cut at the family, not at the id.
"""
from __future__ import annotations


def model_family(model_id: str) -> str:
    """The vendor prefix of a gateway model id: `gemini-gemini-3-8-flash` -> `gemini`.

    A heuristic, and knowingly a rough one: on a gateway the prefix names *who
    serves* the model rather than who built it, so `groq-qwen3-8-27b` reads as
    family `groq` when Qwen is Alibaba's. Harmless as long as nothing else in
    the catalogue is Qwen; a panel drawn from two different hosts of the same
    underlying model would defeat this check, and the fix then is to name the
    panel explicitly rather than to make the heuristic cleverer.
    """
    return model_id.split("-", 1)[0]
