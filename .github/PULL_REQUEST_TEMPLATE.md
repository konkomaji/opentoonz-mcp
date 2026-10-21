## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `python -m ruff check otmcp tests scripts` passes
- [ ] `python -m pytest -m "not slow"` passes
- [ ] If OpenToonz is installed: `python -m pytest -m slow` passes
- [ ] A line was added under `## [Unreleased]` in `CHANGELOG.md` (or the PR is labelled `skip-changelog`)
- [ ] Tools added or changed: `python scripts/gen_tool_docs.py` was run and `docs/TOOLS.md` is committed
