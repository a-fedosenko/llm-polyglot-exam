# Next: the batch page

The single-language page is done. This is what the batch page needs, what is already built for it, and the three decisions that should be settled before it is.

## What is already there

**The tables.** `batch` and `batch_item` exist in `store/models.py`, and `Run.batch_id` joins a batch to the *same* runs the single-language page produces. That join is the point: if a batch had its own parallel record of a parallel pipeline, the two would drift and nobody would notice until the numbers disagreed.

**The engine.** `CapabilityMachine` and `DialectMachine` have no idea whether anything is watching. `runner.run_auto` already drives one to completion on a thread. A batch is a queue of those, and nothing in `engine/` needs to change to support it.

**The skip rule**, in `repo.current_verdict`. Three states, and they are not the same question:

```
nothing stored          never measured           → scan it
current, not approved   measured, not signed off → scan it; a verdict nobody
                                                   has looked at is a draft
current, approved       a person read the evidence and stood behind it
                                                 → skip, unless `rescan_approved`
```

`BatchItem.skip_reason` is there so the progress view can say *why* something was skipped. "Already approved" and "nothing here could be measured" are different answers, and a progress bar that merges them is lying.

## What it has to do

1. **Plan for free.** Group the requested tags into equivalence classes, name the representative of each, say how many classes will actually be probed, how many tags inherit, how many are skipped and why, and what it will cost. The plan must cost nothing — that is what makes it safe to look at before committing to a scan.

2. **Run the queue.** One run at a time: SQLite takes one writer, and two concurrent scans would spend against a budget neither of them set. Items carry their own state so a killed batch restarts where it stopped rather than from zero.

3. **Report honestly while it runs.** `calls_used` must update *during* the batch, not at the end. In the previous tool it stayed at zero until the job finished, which made the progress line useless and was discovered only when somebody tried to use it.

4. **Let a run be opened.** Any item's `run_id` opens the single-language page on that exact run, read-only. That is most of the value of having built the pages against one schema.

## Class collapse, and the thing it quietly costs

Base-language capability is a property of the `(iso639_3, script)` equivalence class, not of the tag: `af`, `af-NA` and `af-ZA` are one experiment. So the ladder runs once per class and the result propagates to the class's other tags, **marked as inherited rather than measured**.

**Inheriting a tier is not inheriting a claim.** A tag that inherits `de`'s tier has said nothing about whether the model marks *that variant*. `Verdict.inherited_from` exists so a reader is never shown an inherited answer as if it had been asked.

And there is a sharp edge the single-language page already surfaces and the batch page must not lose: since the designator names the region, two tags in one class can now produce *different prompts*. `sr-RS-Cyrl` and `sr-ME-Cyrl` share a class whose representative is bare `sr`, which has no region — so a scan asks for plain "Serbian (Cyrillic script)" and four regional variants inherit an answer that was never asked of them. The pre-flight names these as `divergent_siblings`. Closing it is a decision about scan cost, not a bug fix, and it should be made deliberately:

- **leave it** — cheapest, and the inherited rows are honestly labelled
- **probe every tag that diverges** — correct, and multiplies the cost of families like Spanish (22 regional tags) and English (11)
- **make the class key include the region when the designator names it** — the principled version, and a schema-visible change

## Three decisions to settle first

**1. What a dialect batch even iterates over.** A capability scan walks classes. A dialect battery needs a *pair*, so it walks families, and a family with 22 members has 231 pairs. Protocol 021's sample is five families against one sibling each. The likely answer is: one named variety against the family's most-contrastive sibling, chosen once per family — but "most contrastive" is a claim, and choosing it by hand for the families that matter is more defensible than deriving it.

**2. Whether a batch may auto-approve.** It must not, by default: approval is a person reading evidence, and a scan that approved its own output would make the skip rule meaningless. But a 602-locale scan producing 602 drafts nobody will ever open is also not useful. The shape that probably works is a review queue ordered by how much a human pass would buy — borderline rows, split panels, and fabricated markers first.

**3. The budget.** A full capability scan of the previous 199-language set cost ~2,400 calls. `Batch.max_calls` is in the schema and nothing enforces it yet. It should fail closed, and it should be stated in the plan rather than discovered.

## What not to do

**Do not write a second traversal.** The batch runner drives `plan()`/`execute()` exactly as `runner.run_auto` does. If a batch could disagree with the page about what the method does, the page would be a lie told in the project's own voice — and the previous project's conventions record what that drift costs: an analysis harness re-implemented a production call, got it subtly wrong, and 68 bad verdicts reached a protocol's conclusions.

**Do not make a batch skip a control.** The attestor record routes — it decides who is asked first — and it never vouches. The manufactured controls run on every single item, however many times a model has passed them. A stale row must cost wasted routing and never a wrong verdict; the moment a batch is allowed to skip a control to save money, a wrongly-recorded model becomes permanently self-confirming.
