# llm-polyglot-exam

**Which languages — and which dialects — can a model actually produce?** Measured against your own key, one language at a time, with every prompt, reply and judgement on screen before you believe any of it.

Vendor language lists are marketing. "Supports 100+ languages" has no definition behind it, and for the long tail nobody publishes anything at all. This tool measures it instead: it generates text, checks the text locally, has it read back by a reader whose competence in that language has been demonstrated, and grades what survived.

> **Status: the single-language page works end to end.** Both tests, both modes, full persistence. The batch page — the same engine in a loop down a 602-locale list — is the next piece of work, and its tables are already in the schema so the two cannot drift apart.
>
> This is a rewrite of [llm-language-checker](https://github.com/a-fedosenko/llm-language-checker). The method is that project's, validated by twenty-one recorded experiments; what changed is everything around it. See [What changed, and why](#what-changed-and-why).

## The two tests

### Language capability — can it write this language at all?

```
generate → gate (local, free) → back-translate → judge the facts
```

The model is given a *semantic specification* in a pivot language and asked to write it in the target language, choosing its own words — not a sentence to translate (which rewards copying and punishes paraphrase) and not free writing (which lets it fall back on memorised text). A local deterministic gate then checks refusal, copying, script, language identity, confusion with a nearest high-resource relative, degeneration and memorised boilerplate. That gate is free, it runs before any paid call, and it resolves most negatives on its own.

What survives is read back by a **qualified** reader and a blind judge marks which of the specified facts came through. Grading happens entirely in the pivot language, so the judge never needs to read the target language — which is what removes the need for expert graders in six hundred languages.

The number is the measurement. The tier — `Unusable` / `Assisted` / `Proficient` — is a lossy view of it that exists because a workflow has to route on something.

### Dialect support — does it mark *this* variety?

```
generate → judge by control models
```

Five probes, kept separate because they come apart in practice. A model can identify Montenegrin Serbian on sight and still write generic Serbian when asked for it.

| | probe | costs |
|---|---|---|
| **P3** | conversion — rewrite neutral text into the variety; the diff is the measurement | one producer call |
| **P4** | self-consistency — do it again, and compare what moved | one producer call |
| **P1** | blind identification — a control model names the variety, unlabelled | one call per model |
| **P2** | contrast — can it tell two varieties apart? | one call per model |
| **P5** | marker grounding — was every quotation it cited actually in the text? | free |

**P3 and P4 need no control model at all**, so they run first: they are the dialect equivalent of the deterministic negative, and a negative they settle costs nothing further.

**Every control model is re-qualified for the family on every run**, by two controls whose answers are certain *by construction* rather than by authority:

- **identity** — one text shown as both halves of a pair. It cannot be two varieties, so any answer that splits it is the model manufacturing a difference.
- **order** — one real pair shown in both presentations. A reliable answer follows the text; one that follows the slot is position bias, and position bias reads as dialect knowledge exactly where you most want a positive.

A model that fails either is discarded for this family. The stored record of past competence **routes** — it decides who is asked first — and it never vouches: a stale row costs wasted routing, never a wrong verdict.

What the controls cannot catch, stated plainly: a model answering `indistinguishable` to everything passes both. Catching over-merging needs a pair known to be distinguishable, which is the knowledge we lack by assumption. So a null here is reported as **"no evidence of a difference"** and never as "no difference".

## Two modes, one traversal

**Manual** stops before every paid call. You see the exact system and user messages that are about to go out, you can rewrite them, and you can re-run any step you have already taken. **Auto** runs the same steps straight through and you review afterwards.

They are not two code paths. A run is a list of executed steps and nothing else; `plan()` is a pure function of that list and `execute()` only appends to it. Auto mode is literally `while (p := plan()): execute(p)`. That matters because if the page and a batch scan could disagree about what the method does, the page would be a lie told in the project's own voice.

It also makes three things fall out for free:

- **Re-running a step** is truncating the history and planning again. No rollback logic, because nothing was mutated in place. The replaced attempt keeps its database row marked superseded — "the first generation was refused and the second was not" is a finding.
- **Editing a prompt** works because the pre-flight is built with the *production* prompt builder and handed back; what you approved is what goes out, byte for byte.
- **Changing the control panel** mid-run works until the first control has been paid for, after which it would leave competences on screen for a field that no longer exists.

A run whose prompts were rewritten is kept in full — the corpus has every text — and is **never** promoted to a stored verdict. It measured a different instrument, and a number you cannot compare is not a result.

## Run it

Nothing is hosted and nothing is sent anywhere except to the model endpoints you configure. No key is ever stored by the application, written to a result, or entered through a web form.

```bash
git clone <this repo> && cd llm-polyglot-exam
cp .env.example .env                        # settings
cp .models.json.example data/models.json    # your models, endpoints and keys

make install        # venv, backend deps, frontend deps
make bootstrap      # the two assets git cannot ship (~1.6 GB)
make build          # UI into backend/static
make serve          # http://127.0.0.1:8000
```

`make` on its own lists every target.

### In Docker

```bash
make up             # build and start; http://127.0.0.1:${API_PORT:-8000}
make down
```

One image, one process: FastAPI serves the API and the built UI from the same origin, so there is no second service and no reverse proxy.

Two things about the container are worth knowing, because both are failures that do not look like what they are:

- **It runs as you, not as root.** Everything this tool values — the SQLite file, the raw corpus — lives in the `./data` bind mount, and a container writing those as root leaves you unable to read your own results from the CLI. Set `UID`/`GID` in `.env` if yours are not 1000.
- **`./data` is the only thing mounted**, so a `models.json` anywhere else resolves on the host and is simply absent inside. A local model server is reachable at `http://host.docker.internal:<port>/v1`.

### For UI work

```bash
make dev-api        # backend on :8000, reloading
make dev-ui         # Vite on :5173, proxying /api
make mock           # a scripted model endpoint on :9911
```

`make mock` serves an OpenAI-compatible endpoint that answers from a script, so the whole page can be walked end to end with no key and no budget. Everything it returns is fiction — it exists so a change to the traversal can be checked, not as a test double for the method. Point a `data/models.json` at it; the file in `backend/tools/mock_models.py` says how, and why its three ids are from three different families.

### The two assets the repository cannot ship

| | why it is not committed | without it |
|---|---|---|
| **GlotLID**, ~1.6 GB | too large for a repository | the gate checks script but not **language** — a model answering in Indonesian when asked for Acehnese would pass |
| **FLORES-200 controls**, ~25 MB download | CC BY-SA 4.0; share-alike attaches to derived text | only `cv`, `de` and `ru` can have a reader qualified, so nearly every language reports `unverified` |

The page says so on arrival rather than quietly measuring less than it reports. Browsing existing results needs neither.

### Carrying the old corpus across

```bash
polyglot import-corpus ../llm-language-checker/data/llmlc.db
```

Imports the 18,017 raw generations and **not** the verdicts. That is deliberate: every stored verdict in the old database was measured under an older method, so importing them would put rows in the verdicts table that look exactly like fresh measurements and are comparable with none of them. Raw model output never goes stale, and re-grading it offline is the only thing that makes a method change affordable.

## What is stored, and what makes a scan skip a language

Four things, with different lifetimes:

| table | lifetime |
|---|---|
| `generation` | the raw corpus. Append-only, never deleted. The durable asset. |
| `run` + `run_step` | one test as it happened: every prompt, every reply, what it cost. An audit trail, not an answer. |
| `verdict` | the standing answer for (engine, tag, test). Append-only with a `current` flag. |
| `qualification`, `attestor_record` | competence findings reusable across runs, so they are paid once rather than per run. |

**A verdict has three states, and the batch scan reads all three:**

```
nothing stored          never measured          → scan it
current, not approved   measured, not signed off → scan it; a verdict nobody
                                                   has looked at is a draft
current, approved       a person read the evidence and stood behind it
                                                 → skip it, unless "re-scan
                                                   approved" is asked for
```

Approval is a separate gesture from measurement on purpose. A scan that skipped everything merely *current* would skip every draft it had just produced itself.

**Nothing is ever destroyed.** A re-run adds a row and clears the old one's `current` flag; promotion is a reversible pointer move. There is deliberately no confirmation dialog on it — a password on an overwrite still overwrites, and nothing here is destroyed for it to protect.

## What a result does not mean

- **Three items per language**, six more only when the interval straddles the tier cut. Results carry confidence intervals and a borderline one is reported as borderline, never rounded.
- **Results are only comparable within one reader, one judge and one method version.** All three are columns on every verdict, and it is not a formality: the same engine measured through two qualified readers disagreed on tier for 13% of 136 languages.
- **`Assisted` is a wide band.** In the previous programme it spanned fact recall from 0.00 to 0.93 — five languages scored exactly 0.00, fluent text in the right language carrying none of the requested meaning, routed identically to one scoring 0.90.
- **Fact recall saturates**: most items score 1.0, so above the floor it has little resolution.
- **Tier is capability; refusal is willingness.** They are kept apart because the dominant effect across four engines was willingness, not ability: one model refused nothing across 199 languages while another answered `CANNOT` 200 times. On a single scale that would have been reported as "cannot write these languages", which the evidence does not support.
- **Three sentences is the whole sample.** Nothing here speaks to document-length behaviour.
- **Dialect verdicts are the least proven claim in the tool.** See the control limit above.

Anything it reports is evidence, not proof.

## What changed, and why

The method is unchanged and deliberately so — it is twenty-one protocols deep and its decisions are settled. `backend/polyglot/core/` is the previous project's `probe/`, `bt/`, `client/` and `scheme/`, carried over with its reasoning intact. Everything else is new:

| | before | now |
|---|---|---|
| **the traversal** | a generator — `yield` a step, and the caller's only verb is "next" | an explicit step machine whose state is its history, so a step can be re-planned, re-run, or sent with an edited prompt |
| **the page** | one 2,481-line `index.html`, seven tabs, two probes through one form distinguished by hiding fields | React + TypeScript; test type is the first choice and it changes what the page *is* |
| **reading a run** | a scrolling list of step cards, each with a dozen `<dl>` rows to triage | a rail of phases and one step's evidence on the stage; the page itself never scrolls |
| **storage** | `result` rows keyed by instrument, sessions that died with the process | runs and steps persisted as they happen, verdicts append-only with separate currency and approval |
| **what a scan may skip** | freshness | explicit human approval |
| **prompts** | built inside the call | built by a pure function the pre-flight shows you and `execute` sends back |

The one change inside the ported code is additive: every leaf call takes an optional pre-built prompt. `None` means build it here, as before, byte for byte. That seam is what lets the page show you a prompt that is genuinely the one about to be sent, rather than a second construction of it that can drift.

## Documentation

The reasoning lives beside the code, in module docstrings, because a threshold's justification goes stale the moment it is restated somewhere else. Start with:

- `backend/polyglot/engine/types.py` — why a run is a list of steps
- `backend/polyglot/engine/capability.py` — the three rungs, and what each step is for
- `backend/polyglot/engine/dialect.py` — the five probes and the manufactured controls
- `backend/polyglot/store/models.py` — the four lifetimes, and the skip rule
- `backend/polyglot/core/probe/` — the method itself, unchanged

`docs/` in [the previous repository](https://github.com/a-fedosenko/llm-language-checker) carries the full methodology and the twenty-one experiment protocols behind it.

## Licence

MIT. Not commercial, single-operator, and not built to be exposed publicly — there are no accounts, no per-caller quotas and no abuse handling, and the endpoints that spend your API budget are guarded by the port binding alone.
