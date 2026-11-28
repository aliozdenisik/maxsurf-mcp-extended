# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

MCP server (FastMCP, stdio) that drives locally installed Bentley Maxsurf (Modeler, Stability, Resistance, Motions, Multiframe) through COM. Windows x64 and CPython 3.12 64-bit only; dependencies are pinned exactly on purpose (`pyproject.toml`, `requirements.lock`).

## Commands

Use the venv interpreter (`.\scripts\install.ps1 -Python <py312.exe>` creates `.venv`).

```powershell
# Offline suite (no Maxsurf needed): run before every merge (there is no hosted CI)
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -q
# One module / one test
.\.venv\Scripts\python.exe -m unittest tests.test_hydrostatics -v
.\.venv\Scripts\python.exe -m unittest tests.test_module_structure.SchemaSnapshotTests -v
# Remaining pre-merge and release steps
.\.venv\Scripts\python.exe scripts/validate_release.py
.\.venv\Scripts\python.exe scripts/smoke_stdio.py --python .\.venv\Scripts\python.exe --entry server.py
.\.venv\Scripts\python.exe scripts/build_release.py --output-dir dist/<new-dir>
.\.venv\Scripts\python.exe scripts/verify_release_artifacts.py --artifact-dir dist/<new-dir> --python .\.venv\Scripts\python.exe
```

Live checks need installed (and for the smoke, running) Maxsurf; they are not part of offline discovery. See `docs/VERIFICATION.md` for exact arguments:
- `tests.native_wrapper_contract` (run explicitly; `MAXSURF_MCP_MODELER_TYPELIB` selects an install)
- `scripts/check_native_coverage.py --output runtime/<file>.json`
- `scripts/smoke_native_modeler.py` (needs a saved disposable `.msd` and `--confirm-saved-disposable-fixture`)

## Architecture

- `server.py` imports `comcache` first (redirects pywin32 `gen_py` before any COM import), then calls each module in `TOOL_MODULES` via `register_tools(mcp)`. It also re-exports every tool as a module attribute; tests rely on that import surface.
- Each `maxsurf_mcp/<app>.py`, `<app>_controls.py`, `<app>_files.py` owns its tools and ends with `TOOLS = (...)` and `register_tools(mcp) -> guard.register(mcp, TOOLS)`.
- Every tool is wrapped by `guard.guarded(classification=..., module=..., channel=..., tier=...)`, the single chokepoint for: tier gating, audit (JSONL, intent + completion for mutating/diagnostic calls), error normalisation into typed `errors.MaxsurfError` (never return `{"error": ...}` as success), and dispatch to the COM thread. `guard.make_adapter` wraps it in an async adapter for FastMCP; the guarded function stays sync and is what tests call.
- `comthread.py`: one owned STA thread runs all COM calls serially. Timeouts do not cancel: the worker goes `abandoned` and refuses new work until the stuck call returns.
- `session.py` / `com.py`: per-module application sessions, attach policy (`MAXSURF_MCP_ATTACH_POLICY`, default requires the app already running), liveness probing.
- `workspace.py`: file paths must be inside `MAXSURF_MCP_WORKSPACE_ROOTS`.
- `config.py`: all policy from `MAXSURF_MCP_*` env vars, read per call, fail-closed defaults.
- Tiers: default tools; `diagnostics` (raw COM, `native_call`, etc.) only with `MAXSURF_MCP_ENABLE_DIAGNOSTICS=1`; `legacy.py` quarantined tools (target COM members that do not exist, kept verbatim, not to be "repaired" by guessing) only with `MAXSURF_MCP_ENABLE_LEGACY=1`. Disabled tiers are absent from `tools/list`.
- `native_inventory.py` / `native_automation.py` / `native_call_policy.py` / `native_types.py` / `native_enum.py`: installed type-library discovery and metadata-validated native calls; every installed member is either a route or an explicit boundary (`check_native_coverage.py` reconciles this).

## Contracts that tests enforce

- `tests/data/tool_schemas.json` is a committed snapshot of every registered tool's input schema. Any tool add/remove/signature change must update it in the same commit; `validate_release.py`, `smoke_stdio.py` and the artifact verifier also read it.
- `tests/test_module_structure.py` pins the `TOOL_MODULES` list and the exact tool surface for each diagnostics/legacy combination (run in fresh subprocesses).
- Version string lives in `server.VERSION`, `pyproject.toml`, `extension/manifest.json`, `plugins/maxsurf-mcp/.codex-plugin/plugin.json` and `scripts/smoke_stdio.py`; bump all together (`validate_release.py` asserts manifest == pyproject).
- `build_release.py` packages an allowlist; `runtime/`, `gen_py`, `.venv`, `build`, `dist` are never shipped. Bentley software, manuals and samples must not enter the repo or archives.

## Production checkout and worktree policy (MANDATORY)

The primary checkout (the clone that Claude and Codex MCP configurations point at with `.venv/.../python server.py`) is the production install. Every server start fast-forwards it to `origin/main` (`maxsurf_mcp/autoupdate.py`; user-facing description in `docs/INSTALL.md`, "Git checkout installations: automatic updates").

- Keep the primary checkout on `main` with no tracked edits. Never run `git switch`/`checkout`, `reset`, `stash`, `rebase` or commits in it. Any of these either stops the auto-update (it updates only a clean `main`) or changes the code that newly started sessions load.
- Do all development, including one-line fixes, in a separate worktree on a feature branch, then open a PR to `main`:

  ```sh
  git fetch origin
  git worktree add .claude/worktrees/<name> -b <branch> origin/main
  ```

  `.claude/` is gitignored. Run tests in the worktree with the primary checkout's interpreter (`../../../.venv/Scripts/python.exe`) or your own venv. After the merge, run `git worktree remove .claude/worktrees/<name>` and delete the branch.
- `main` can be checked out in only one worktree. Never check `main` out in a development worktree; that detaches the primary checkout and stops its updates.
- A dependency change merged to `main` is installed at the first server start when no other server of the checkout is running. Until then, every start defers the whole update (`update=deferred`).
- To verify which code each running server loaded, run `python -m maxsurf_mcp.autoupdate` from the primary checkout. Never stop a server or its session to force an update: running analyses keep their code until the session restarts its server.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `aliozdenisik/maxsurf-mcp-extended` (via `gh`). See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
