# Agent Instructions

## 1. Project source of truth

* Work only inside this repository unless Saqib explicitly instructs otherwise.
* Inspect the current repository state before proposing or implementing work.
* `docs/` is the source of truth for architecture, design decisions, development history, and documented project behavior.
* Read the relevant documentation and existing implementation before making architectural assumptions.
* Do not rely on memory from another session, model, or previous task when the repository can answer the question.

## 2. Default workflow: inspect → recommend → approve → implement

* Do not begin implementation automatically.
* First inspect the relevant code, tests, documentation, and current project state.
* Recommend the **next logical task** that advances the project in the correct dependency/order sequence.
* Explain briefly:

  * why this task should be next;
  * what it depends on;
  * what part of the system it affects;
  * the likely files/modules involved;
  * what completion should look like.
* Prefer one well-scoped next task over a large collection of future tasks.
* Wait for Saqib's approval before modifying code or documentation for a newly proposed task. A direct instruction to implement a named task, or `approved`/`proceed` in response to its proposal, is approval for that task.
* Once a task is approved, carry it through implementation, relevant tests, database verification, documentation, and packaging without asking for permission at each step. Ask again only for a genuinely unresolved product/architecture choice or a material expansion beyond the approved outcome.
* A task that is partly implemented on the current branch remains approved. Verify and reuse the existing work, then finish the remaining accepted scope; do not restart the approval cycle solely because the baseline advanced.

## 3. Scope control

* Work only on the approved task.
* Do not add unrelated features, refactors, abstractions, cleanup, or architectural changes.
* Do not expand the task because another improvement appears useful.
* If an additional issue is discovered:

  * record/report it;
  * explain whether it blocks the approved task;
  * do not implement it unless it is required for the approved task or Saqib approves the expanded scope.
* Prefer the smallest complete change that fits the existing architecture.

## 4. Never guess project decisions

* Inspect existing code and documentation before deciding how a component should behave.
* If a genuinely unresolved product or architectural decision is required, ask Saqib rather than inventing one.
* Do not reinterpret previously documented decisions unless the current task explicitly requires reconsidering them.
* Existing names, contracts, schemas, event flows, and module boundaries should be preserved unless changing them is part of the approved task.

## 5. Multi-agent / multi-model safety

* Assume another Codex, Claude, ChatGPT, or human session may have changed the repository since this session last inspected it.
* Never treat another session's memory as authoritative; re-check the repository.
* Before implementation, inspect:

  * current branch;
  * `git status`;
  * relevant recent commits;
  * relevant documentation/decision-log state.
* Before finalizing the task, check repository state again.
* Never overwrite, revert, discard, or "clean up" changes that were not created as part of the approved task.
* Distinguish committed changes on the current base from uncommitted or concurrent changes. Committed code is the current baseline: inspect, test, and reuse it even if a prior task description is stale. A documentation gap by itself does not require renewed approval; correct it within the approved delivery and describe the provenance accurately. Never claim a pre-existing component was created by this task.
* If unexpected uncommitted or concurrently changing work overlaps files needed by this task, preserve it and report the collision. Continue independent parts when safe; ask Saqib only when the overlap cannot be reconciled from the repository and approved scope. Do not overwrite another session's changes.
* Never use destructive commands such as `git reset --hard`, `git clean`, or broad file restoration to resolve another session's work unless Saqib explicitly instructs it.

## 6. Documentation must move with the code

- Code changes and required documentation updates belong in the same task.
- Update existing canonical documentation rather than creating competing documents.
- Keep documentation consistent with the actual implementation.
- Every delivery must update `CHANGES.md` and `TESTING.md`.
- When a decision or architecture changes, also update `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, and the relevant architecture document in the same delivery.
- If implemented code is missing from documentation, verify its behavior and document the as-built state in the current delivery. Do not silently treat undocumented code as a new design decision or duplicate its implementation.

### Decision-log integrity

- Use the delivery slug as a temporary identifier during parallel work. Immediately before packaging/applying the delivery, re-check the latest GitHub `main` and assign a final number only if a new decision is needed.
- Confirm that `docs/decisions/INDEX.md`, the tail of `confirmed-decisions.md`, and the decision archive filenames agree on the latest number.
- Append each new decision at the true end of `confirmed-decisions.md`, in numerical order, using the heading format `### N. Title`.
- Existing decision content is immutable. Correct a decision by adding a new entry that references the original; never rewrite its substance.
- Formatting or physical-order repairs must preserve the existing decision body exactly and must be documented by a new correction entry.
- A reference to a decision number absent from the canonical log is an inconsistency, not proof the decision exists. Check the current base and concurrent work, then correct the reference or add the properly numbered decision at packaging. Never invent a historical decision or renumber an existing one.

### Architecture diagrams

- Architectural changes require diagrams showing:
  - data flow between affected components or modules;
  - relevant internal flow inside changed modules.
- Use the repository’s established ASCII code-fence diagram style.
- Diagrams must explain the system and must not be added merely as decoration.

## 7. Decision-log discipline

* Follow the repository's documented decision-log process.
* Never trust a proposed decision number from a prompt, memory, or another agent.
* Immediately before assigning a decision number, re-check the current canonical decision index/log.
* If the number has already been used, use the next valid free number.
* Do not create a new architectural decision when the change is already covered by an existing decision.

## 8. Implementation discipline

* Reuse existing project patterns before introducing new ones.
* Do not duplicate functionality that already has a canonical implementation.
* Keep modules focused on their documented responsibilities.
* Preserve established interfaces unless changing them is required by the approved task.
* Add or update tests appropriate to the changed behavior.
* Run the relevant tests/checks after implementation.
* Do not hide failing tests or weaken assertions merely to make the suite pass.

### Standing authorization for tests and development databases

* Saqib authorizes Codex to run any relevant `pytest` command, including the full suite, without per-command approval. This covers setup, fixtures, reruns, and test-created records.
* Saqib authorizes database connections, reads, inserts, updates, deletes, schema inspection, migrations, and test setup/teardown needed to implement and verify an approved task in the project's local development and test databases. Use the project's configured database conventions and isolate test data where appropriate. No per-query or per-test approval is needed.
* This authorization does not extend the task's functional scope or authorize changing live trading/broker accounts, external production databases, or deleting unrelated persistent data. Report the target database and any material effect when those boundaries are unclear.

## 9. When something outside scope is discovered

Classify it as one of:

* **Blocker** — approved task cannot be completed correctly without resolving it.
* **Related follow-up** — useful next task but not required now.
* **Unrelated** — leave untouched.

Only blockers may be addressed automatically, and only to the minimum extent necessary. Report related follow-ups to Saqib as possible next tasks.

## 10. Task completion

When handing work back:

* summarize what changed;
* explain the functionality of any new component;
* identify documentation updated;
* report tests/checks run and their results;
* mention any known limitations or unresolved blockers;
* identify any useful follow-up work, but do not implement it automatically.

Do not claim completion if implementation, documentation, or required validation is still unfinished.
