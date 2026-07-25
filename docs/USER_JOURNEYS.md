# FLOTILLA user journeys

These journeys map product roles to the static landing page and deterministic
browser simulator. The simulator uses temporary in-memory state; the registered
SQLite lineage and generated evidence remain unchanged below it.

## 1. Research director allocates a fixed budget

**Intent:** Give every thesis a fair falsifier, then concentrate capital on
survivors without losing reserve visibility.

- **Landing page:** “Capital visualization” explains the five-unit fair floor,
  six-unit follow-up round, and one-unit reserve. “System architecture” shows
  where scheduling and the budget ledger enforce the rule.
- **Demo actions:** Choose a scenario, set the portfolio mandate, launch all
  falsifiers, use **Advance one experiment**, select a live thesis, and use
  **Reallocate 1 unit**. The capital chart separates spent, earmarked, and
  reserve.
- **Success state:** A falsifier survives, a follow-up completes, and the
  course ends `SURVIVED`.
- **Kill state:** A predicate fires, the director sees `PENDING_KILL`, and no
  later experiment runs until a reviewer decides.
- **Revive state:** Capital can return to a killed thesis only after a recorded
  `REVIVE`; the earlier stop remains in lineage.
- **Undetermined state:** In “Thin evidence,” T-05 lacks a required score. No
  capital moves automatically and no directional claim is inferred.

## 2. Thesis owner proposes the cheapest falsifier

**Intent:** Register what would change the team’s mind before asking for more
compute.

- **Landing page:** “The portfolio problem” and “Operating doctrine” describe
  executable rules, approved plans, deadlines, caps, and limitations.
- **Demo actions:** Select a thesis from the selector or trajectory lane. The
  evidence drawer shows its prediction, kill predicate, scores, budget cap, and
  limitations. Launch and advance to execute the one-unit falsifier.
- **Success state:** The predicate holds; the thesis becomes
  `FALSIFIER_SURVIVED` and its registered follow-up joins the back of the queue.
- **Kill state:** The predicate fires from recorded scores and produces
  `PENDING_KILL`; the owner cannot silently continue.
- **Revive state:** A new registered arm can justify revival in the “Signal
  recovery” scenario.
- **Undetermined state:** Missing predicate inputs yield `UNDETERMINED`, giving
  the owner a precise evidence gap rather than a fabricated verdict.

## 3. Reviewer approves or overturns a kill

**Intent:** Make stopping falsifiable, human-gated, and reversible.

- **Landing page:** “Fair floor,” “Executable rules,” and “Reversible
  governance” define the separation between model proposal, executable
  predicate, and human confirmation.
- **Demo actions:** Select the pending thesis, inspect the evidence drawer, then
  choose **Confirm stop** or **Overturn stop**. A confirmed stop enables
  **Revive thesis** later; an overturn queues one registered follow-up.
- **Success state:** The reviewer confirms evidence is sufficient and the stop
  returns earmarked capital to reserve.
- **Kill state:** `KILL_CONFIRMED` appears with the thesis and released amount.
- **Revive state:** `REVIVE` appends a challenge and queues a follow-up without
  deleting `KILL_CONFIRMED`.
- **Undetermined state:** The reviewer may leave the thesis unresolved or use
  **Fund follow-up** to append `UNDETERMINED_ESCALATED`.

## 4. Operator audits lineage

**Intent:** Reconstruct what ran, what it cost, what evidence was used, and who
changed the decision.

- **Landing page:** “Bundled Journey 0” states the mechanism claims and their
  limits; “System architecture” identifies the SQLite ledger and artifacts.
- **Demo actions:** Watch **Simulation lineage** evolve after every launch,
  run, reallocation, stop, overturn, and revive. Expand a thesis evidence
  dossier for registered code commit, data hash, seed, predicate, limitations,
  and decisions. Expand **Decision timeline** for all 43 immutable fixture
  events.
- **Success state:** Run, budget, and decision records agree and a promoted
  thesis retains complete provenance.
- **Kill state:** The audit shows `PENDING_KILL` before `KILL_CONFIRMED`, never
  an unexplained terminal status.
- **Revive state:** Both stop and revival remain visible in order.
- **Undetermined state:** The log names the missing evidence and records that no
  verdict was inferred.

