# Changelog

## 0.2.0-rc.7 (pre-release, 2026-11-28)

### Added

- Git checkout installations update themselves at every server start: a locked, fast-forward-only `origin/main` update that never resets or switches branches, installs changed dependency pins first, and waits while other servers of the checkout are running. Each running server records its commit, branch, start time and PID; `python -m maxsurf_mcp.autoupdate` lists them (`docs/INSTALL.md`, [PR #11](https://github.com/aliozdenisik/maxsurf-mcp-extended/pull/11)).

### Fixed

- `open_design` refuses a Git LFS pointer file before any COM call ([PR #9](https://github.com/aliozdenisik/maxsurf-mcp-extended/pull/9)).

## 0.2.0-rc.6 (pre-release, 2026-11-26)

First public GitHub release. The package, requirements and verification limits are described in the [release notes](https://github.com/aliozdenisik/maxsurf-mcp-extended/blob/0.2.0-rc.6/docs/RELEASE_NOTES_0.2.0-rc.6.md) and [Release status](docs/RELEASE_STATUS.md).

### Changed

- One Native enum module (`maxsurf_mcp/native_enum.py`) now resolves every Maxsurf enum name and number for all tools, keyed by the manuals' enum type names, with numbers taken from the live type library ([#1](https://github.com/aliozdenisik/maxsurf-mcp-extended/issues/1), [PR #8](https://github.com/aliozdenisik/maxsurf-mcp-extended/pull/8)).
- Unknown enum members return one error shape (`field`, `requested`, `enum_type`, `available`) from every tool; `available` lists only that type's members.
- Enum member names are matched case-insensitively and trimmed.

### Fixed

- Prefix collisions between enum types no longer leak unrelated members into errors or results.
- Unknown Motions results-table errors list only exportable `skTab` tables.
