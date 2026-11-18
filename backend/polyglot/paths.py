"""Where things live on disk.

One module, so a relocated data directory is one edit rather than nine. The old
project computed every path as `parents[3]` from the importing module, which
meant moving a file two directories changed what it could read — and the failure
was silent, because a missing control file reads as "this language cannot be
verified" rather than as a broken install.

`POLYGLOT_DATA_DIR` overrides the writable half (corpus, models, database);
the read-only authored assets stay beside the repository that version-controls
them.
"""
from __future__ import annotations

import os
import pathlib

#: Repository root: backend/polyglot/paths.py -> backend/polyglot -> backend -> root
ROOT = pathlib.Path(__file__).resolve().parents[2]

DATA = pathlib.Path(os.environ.get("POLYGLOT_DATA_DIR") or (ROOT / "data")).resolve()

# -- authored, version-controlled: the instrument ------------------------------
SCHEMES = ROOT / "schemes"
MARKERS = ROOT / "markers"
#: Which variety each family is contrasted against. Beside `markers/` because
#: it is the same kind of thing: an authored claim that belongs in review, not
#: a local setting. One file for every locale list — see `core/reference.py` —
#: and a file of the same name under the data directory overrides it, for
#: working on a decision before proposing it.
REFERENCES = ROOT / "references"
SPECS = DATA / "specs" / "specs.json"
CONTROLS = DATA / "controls"

# -- produced, regenerable -----------------------------------------------------
CORPUS = DATA / "corpus"
MODELS = DATA / "models"
CACHE = DATA / "cache"
UNICODE = DATA / "unicode"

GLOTLID = MODELS / "glotlid_v3.bin"
SCRIPT_TABLE = UNICODE / "scripts.json"
DB = DATA / "polyglot.db"


def ensure() -> None:
    for d in (CORPUS, MODELS, CACHE, UNICODE, CONTROLS, SPECS.parent):
        d.mkdir(parents=True, exist_ok=True)
