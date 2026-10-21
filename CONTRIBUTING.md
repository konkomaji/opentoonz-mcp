# Contributing

Thanks for helping. This project sits between AI assistants and a real production tool, so I care most about two things:

- **Correctness against OpenToonz.** The tool behaves the way OpenToonz behaves.
- **Never losing an animator's work.**

## Setup

```bash
git clone https://github.com/konkomaji/opentoonz-mcp
cd opentoonz-mcp
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
```

The render tests need a local OpenToonz install. The server finds it automatically; to point at a specific install, set `OPENTOONZ_HOME` and `OPENTOONZ_STUFF`.

## Checks to run before opening a PR

```bash
python -m ruff check otmcp tests scripts
python -m pytest -m "not slow"     # fast, no OpenToonz needed (this is what CI runs)
python -m pytest -m slow           # renders through your local OpenToonz
python scripts/gen_tool_docs.py    # if you added or changed a tool
```

## Rules of the road

1. **Prove format behaviour.** Any change to how `.tnz` or `.pli` data is written needs two things:
   - a reference to the OpenToonz source file and function that reads it;
   - a test that renders through `tcomposer` and checks the pixels.

   `tests/test_integration.py::test_evaluator_matches_opentoonz_render` shows the pattern.
2. **Every bug gets a regression test.** Put format, vector and server bugs in `tests/test_regressions.py`. Put bugs that need a real install in `tests/test_server_regressions.py`.
3. **Keep edits transactional.**
   - Tools that change a scene must go through `Ctx`: it takes the snapshot, holds the lock, verifies the result and writes the journal.
   - Never write scene files directly from a tool.
   - Never delete a user's `.pli` without a snapshot.
4. **Units at the API edge.**
   - Tools take pixels with +y pointing down, 1-based frames and degrees.
   - Internally everything is OpenToonz inches with +y up, and frames are 0-based.
   - Convert only in `server.py`.
5. **Changelog.** Add a line under `## [Unreleased]` in `CHANGELOG.md` for every user-visible change. CI enforces this; label the PR `skip-changelog` for docs-only work.
6. **Commits.** Use the imperative mood and a short subject. Explain *why* in the body when it isn't obvious.

## Where things live

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the module map and the per-call transaction, and [docs/FORMAT_NOTES.md](docs/FORMAT_NOTES.md) for the OpenToonz file-format facts the code relies on.
