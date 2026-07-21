# Round 13 — Disposition and Suppression

## Objective

Add user-controlled advisory annotations to specific finding occurrences
(fingerprints) within a project, without changing severity, counts, or
default exit codes. Dispositions are metadata that let users acknowledge,
accept risk, or mark false positives for audit trail purposes.

## Design constraints

1. **Purely advisory by default**: Dispositions NEVER reduce severity,
   change finding counts, or affect the default CLI exit code.
2. **Opt-in CI integration**: Only when `--respect-dispositions` is passed
   does the CLI exclude accepted high/critical findings from the gate.
3. **Append-only immutability**: Disposition records cannot be edited or
   deleted, only overlaid by newer records.
4. **Mandatory expiry**: Every disposition must have an expiry date (max
   180 days). Expired dispositions are treated as non-existent.
5. **Project-scoped**: Dispositions apply only within one project; the
   same fingerprint in different projects has independent disposition.
6. **Coverage-honest**: Findings with state `resolved` or
   `unknown_due_to_coverage` cannot be dispositioned (they're already
   gone or uncertain).

## Disposition statuses

- `acknowledged`: "I've seen this and will track it elsewhere."
- `accept_risk`: "I understand the risk and choose to proceed."
- `false_positive`: "This is a detection error, not a real issue."
- `wont_fix`: "This is real but we've decided not to fix it."

## Implementation plan

### 1. Storage layer

- Store in `.verity-data/projects/<aid>/dispositions/<fingerprint>.json`
- Each file is an append-only array of disposition events
- Schema: `{status, expiryDate, note?, createdAt, createdBy}`
- Validation: bounded strings, ISO dates, max 200-char notes
- Limits: max 256 dispositions/project, max 32 events/fingerprint

### 2. History module updates

- Add `DispositionRecord` type and validation
- Add `add_disposition(project, fingerprint, status, expiry, note)`
- Add `list_dispositions(project)` returning effective dispositions
- Extend `diff()` to include `disposition` field per change

### 3. CLI updates

- `verity project dispose --project <ref> --fingerprint <fp> --status
  <status> --expiry <days> [--note <text>]`
- `verity project dispositions --project <ref>` lists all active
- `verity project review --respect-dispositions` respects accept_risk
  when computing gate

### 4. Web API

- `POST /api/projects/{ref}/dispositions/{fingerprint}` with
  `{status, expiryDays, note?}`
- `GET /api/projects/{ref}/dispositions` returns all active
- Diff response gains `disposition: {status, note, expiresAt}` per change

### 5. Web UI

- Add small "Dispose" button on existing/changed findings in diff view
- Show disposition badge if already disposed
- Simple form: status dropdown, expiry days (default 30), optional note

## Acceptance criteria

### Core behavior

1. Disposition on resolved/unknown_due_to_coverage is rejected
2. Expired dispositions don't appear in diff or CLI list
3. Default review/diff behavior unchanged (dispositions invisible unless
   queried)
4. `--respect-dispositions` makes accept_risk findings not fail the gate

### Safety

1. Symlinks, dangerous permissions, corrupt JSON rejected
2. Fingerprints must be exactly 64 hex chars
3. Notes sanitized (no control chars, max 200)
4. Creation rate-limited (max 100 disposition events/minute/project)

### UX

1. CLI gives clear feedback: "Marked <fp> as false_positive until <date>"
2. Web shows badge + hover tooltip with note
3. Diff counts gain a "notedCounts" block showing disposed findings by
   status

## Out of scope

- Pattern-based suppression (e.g., "ignore all SQL in tests/")
- Changing severity or removing findings from reports
- Cross-project disposition inheritance
- Disposition history UI (only current effective state shown)