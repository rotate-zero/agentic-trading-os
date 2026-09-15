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
* Wait for Saqib's approval before modifying code or documentation.
* Approval such as `approved`, `proceed`, or an explicit instruction to implement the proposed task authorizes only that scope.

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
* If unexpected existing changes overlap files needed by this task, stop implementation and report the collision to Saqib.
* Never use destructive commands such as `git reset --hard`, `git clean`, or broad file restoration to resolve another session's work unless Saqib explicitly instructs it.

## 6. Documentation must move with the code

* Code changes and required documentation updates belong in the same task.
* Update existing canonical documentation rather than creating competing documentation.
* Keep documentation consistent with the actual implementation.
* For architectural work, include/update diagrams showing:

  * data flow between affected components/modules;
  * relevant internal flow inside changed modules.
* Diagrams should explain the system, not decorate the documentation.

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
