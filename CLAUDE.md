# CLAUDE.md

Project: **FloodNet — Urban Flood Nowcasting System**.

## Read first

- `docs/PRD.md` — before any product decision.
- `docs/REQUIREMENTS.md` — for what the problem statement actually requires, and for requirement traceability.
- `docs/DECISIONS.md` — for what has and has not been decided. **Currently: everything is UNDECIDED.**

## Rules

1. **Never fabricate.** No invented data, results, metrics, model performance, scientific facts, or citations. If something is unknown, say it is unknown.
2. **Verify before relying.** External repositories, datasets and APIs must be inspected and licence-checked before use. Unverified things are marked `[UNVERIFIED]`.
3. **Label synthetic data.** Anything assumed, synthesised or placeholder must be labelled as such in code, API output, UI and presentation.
4. **Do not implement ahead of the plan.** No code, dependencies, clones or architecture before the relevant planning stage is authorised.
5. **Do not decide what is undecided.** Record decisions in `docs/DECISIONS.md` with evidence; never assume a decision already made.
6. **Separate problem-statement requirements from our ideas.** Never present a [PROPOSED] feature as something the problem statement required.
7. **Optimise for a working 48-hour prototype.** Prefer the simplest thing that satisfies a requirement; complexity needs a requirement to justify it.
8. **Keep documentation modular.** Small, single-purpose documents. This file stays minimal — content belongs in `docs/`.
