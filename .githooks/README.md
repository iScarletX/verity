# .githooks — opt-in local git hooks

These hooks are **not** installed automatically. Nothing in the
project modifies your global git config. If you want the hooks in a
particular clone, enable them explicitly:

```bash
git config core.hooksPath .githooks
```

To turn them off again:

```bash
git config --unset core.hooksPath
```

Both operations are local to your clone and never touched by CI.

## What's here

- `pre-push` — runs `python3 tools/verify_repo.py --require-clean`
  before every push. If the gate fails, the push is aborted. Bypass
  in an emergency with `git push --no-verify`; GitHub Actions still
  runs.

The authoritative gate is always CI (`.github/workflows/ci.yml`),
regardless of whether the local hook is enabled.
