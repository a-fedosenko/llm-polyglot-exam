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
METHOD_VERSION = "4.0.0"


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
    aggregator_model_list_url: str | None = None

    #: Defaults the UI pre-selects. Every one of them is overridable per run
    #: from the page, which is the point of the page.
    judge_model: str = "openai-gpt-4o-mini"
    backtranslator_panel: list[str] = ["gemini-gemini-3-6-flash",
                                       "deepseek-deepseek-v4-flash"]
    pivot: str = "en"

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
