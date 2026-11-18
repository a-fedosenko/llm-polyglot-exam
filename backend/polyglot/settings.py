"""Runtime configuration.

Secrets come from the operator's own `.env` and are never persisted into a
result, written to the database, or returned by an endpoint. `redacted()` is
what the API serves.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from polyglot.paths import ROOT

#: Bumped whenever a change would make a new measurement incomparable to an old
#: one -- a changed prompt, a moved threshold, a different gate rule. It is part
#: of a verdict's identity, so a bump does not destroy anything: it makes every
#: stored verdict *stale* rather than wrong, and the UI says so.
#: 5.0.0 — P3 became a 2×2 between the two arms, replacing conversion from the
#: umbrella (which was the model's own default variety, not a neutral source)
#: and absorbing P4; a battery whose two arms come back identical stops free;
#: a silent panel reports `indeterminate` rather than `no-evidence`; and
#: confidence counts votes rather than panel members.
METHOD_VERSION = "5.0.0"


class Settings(BaseSettings):
    # The repository root, not the working directory: `polyglot serve` is run
    # from wherever the operator happens to be, and a config file that is only
    # found from one directory is a config file that silently does not apply.
    model_config = SettingsConfigDict(env_file=(ROOT / ".env", ".env"),
                                      env_prefix="", extra="ignore")

    #: The operating locale list. `catmint` is the 602-tag TMS list whose
    #: dialects are named explicitly and whose script subtag appears last and
    #: only where it discriminates -- `sr-me-cyrl`, but `en-gb`.
    #:
    #: **This is not the catalogue back-translator controls resolve against.**
    #: A control tag is always the public `default` catalogue's, because FLORES
    #: is published in canonical BCP-47 and a locale list is not a catalogue.
    scheme: str = "catmint"

    #: A JSON file listing models, each with its own endpoint and key. The
    #: general route and the only one a new user needs.
    custom_model_list_path: str | None = None

    #: One gateway serving every model id. Narrower, still supported; the JSON
    #: file wins where both are configured.
    aggregator_base_url: str | None = None
    aggregator_admin_api_key: str | None = None

    #: Where to ask a gateway what models it serves, when it does not answer
    #: the standard `GET {base_url}/models`. Only a hint about *discovery* —
    #: which models to use is never configured here.
    aggregator_model_list_url: str | None = None

    #: The language everything is back-translated into, so the judge never has
    #: to read the target language. Installation-level because it is a property
    #: of who is operating the tool, not of a measurement: a language that *is*
    #: the pivot is automatically measured through a fallback.
    pivot: str = "en"

    # **The judge and the reader panel are deliberately not here.**
    #
    # They were, and that was wrong. A verdict is only comparable to another
    # measured through the same judge and the same reader — the tool stores both
    # on every row and refuses to compare across them — so they are the most
    # identity-defining part of a measurement there is. Setting them once in an
    # env file and never seeing them again is how a run comes to be graded by
    # something nobody remembers choosing.
    #
    # They are chosen per run, from the models the configuration can actually
    # reach, and they are shown on screen while the run is set up rather than
    # hidden behind a panel. `GET /api/models` proposes a starting pair and says
    # what rule produced it; nothing is defaulted silently.

    database_url: str | None = None
    api_bind: str = "127.0.0.1"
    api_port: int = 8000

    #: Hard ceiling on what one run may spend, whatever the page asks for.
    #: A single-language run costs 9-15 calls for capability and ~20 for a
    #: three-attestor dialect battery; the ceiling exists to bound a loop that
    #: has gone wrong, not to stop the tool doing its job.
    max_calls_per_run: int = 200

    def redacted(self) -> dict:
        d = self.model_dump()
        for k in list(d):
            if "key" in k and d[k]:
                d[k] = "[redacted]"
        return d


settings = Settings()
