# Releasing

I cut releases from `main` by pushing a version tag. Automation handles everything after the tag, so a release is only as good as the checklist below.

## Checklist

1. **Changelog.** Move the lines under `## [Unreleased]` into a new section `## [X.Y.Z] - YYYY-MM-DD`. Keep an empty `## [Unreleased]` above it, and update the compare links at the bottom.
2. **Bump the version** in all four places:
   - `pyproject.toml` (`version`)
   - `otmcp/__init__.py` (`__version__`)
   - `server.json` (`version` and `packages[0].version`)
3. **Check locally:**
   ```bash
   python scripts/release_check.py --tag vX.Y.Z
   python -m ruff check otmcp tests scripts
   python -m pytest              # includes the OpenToonz render tests
   python scripts/gen_tool_docs.py --check
   ```
4. **Commit, tag, push:**
   ```bash
   git commit -am "Release X.Y.Z"
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

## What the release workflow does (`.github/workflows/release.yml`)

1. `release_check.py` refuses the tag unless it, the three version fields and a dated changelog section all agree.
2. It builds the sdist and wheel, and runs the unit tests on Windows.
3. It publishes to PyPI with trusted publishing (OIDC; no stored token).
4. It waits for PyPI to serve the version, then publishes `server.json` to the MCP Registry with `mcp-publisher login github-oidc`.
5. It creates the GitHub release, using that version's changelog section as the notes.

## One-time setup (repository owner)

- **PyPI:** add a trusted publisher for project `opentoonz-mcp`, with owner `konkomaji`, repository `opentoonz-mcp`, workflow `release.yml` and environment `pypi`.
- **GitHub:** create an environment named `pypi` (optionally with required reviewers).
- **MCP Registry:** the name `io.github.konkomaji/opentoonz-mcp` is verified by GitHub OIDC. PyPI ownership is verified by the `mcp-name:` marker in `README.md`.
