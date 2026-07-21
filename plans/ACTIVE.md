# No active implementation round — real-model result and maintainer decision required

Round 18 implemented the synthetic real-model semantic quality protocol, but no
trusted research credential was present. Therefore:

- no real external model was called;
- no real `modelQualityMeasured=true` report exists;
- sealed test protocol v1 has **not** been consumed;
- fixed contract replay remains `modelQualityMeasured=false`.

## Allowed next action without a code round

The maintainer may intentionally provide a trusted API credential through a
named environment variable and approve a local research run. Run calibration
first, then selection. Freeze the role prompts, model ids, temperature, egress
policy, repetition count and budgets before asking whether to consume sealed
test. Real reports stay local under gitignored `.verity-data/model-evals/` and
must be reviewed before any capability or release claim changes.

## Proposed Round 19 — not approved, do not implement

Explainable score and remediation/re-review loop:

- one severity-capped 0–100 safety score only for checks actually completed;
- a separate review-confidence/coverage grade so missing checks never look safe;
- every deduction traceable to risk id, root cause and unresolved disposition;
- controlled remediation proposals (`append`, `insert_after`, `replace`,
  `delete`) tied to Evidence and never auto-applied;
- re-review proves resolved/new/persistent/unknown status and refuses score
  comparison when profile, formula version or relevant coverage differs;
- no Critical/High finding can be averaged away by many passing checks.

This proposal borrows validation-gate and multidimensional-reporting principles
from SkillOpt, GameWorld, VideoGameQA-Bench, DSGBench, TALES and related public
benchmarks. Those projects are method references only, not dependencies or
security evidence.

Do **not** start Round 19, Provider/OpenRouter Web productization, default
semantic enablement, V1 closure, V1.5 Prompt black-box execution, or V2 Skill
sandbox execution without a new maintainer-approved plan.
