# Maxsurf MCP 0.2.0-rc.7

Release candidate for connecting Codex and Claude Desktop to locally installed Bentley Maxsurf applications.

**Technical preview.** This package does not claim complete native-suite coverage, production engineering approval or vessel/class certification.

## Install

| Client or method | Asset | Instructions |
| --- | --- | --- |
| Claude Desktop extension | `maxsurf-mcp-0.2.0-rc.7-win-x64-py312.mcpb` | [Claude setup](INSTALL.md#claude-desktop-extension) |
| Codex plugin | `maxsurf-mcp-0.2.0-rc.7-codex-win-x64-py312.zip` | [Codex setup](INSTALL.md#codex-plugin) |
| Source through PowerShell | `maxsurf-mcp-0.2.0-rc.7-source.zip` | [Source installation](INSTALL.md#powershell-source-installation) |

Windows x64, CPython 3.12 x64 and separately licensed Bentley applications are required. The extension/plugin bundles include Python dependencies; Python and Bentley software are installed separately.

For Claude, select the MCPB during installation, then select `python.exe` and a design workspace in Configure. For Codex, extract the dedicated Codex ZIP and register its local marketplace. Open only the Bentley module required for the task.

## What changed since 0.2.0-rc.6

### Git checkout installations update themselves

When a client starts `server.py` from a `git clone` on `main`, every server start now brings that checkout up to date with `origin/main` before any code loads. You no longer need to run `git pull`. MCPB, Codex plugin and source ZIP installations are not git checkouts and are unchanged.

- A lock that belongs to the checkout serialises concurrent session starts. The update is `git fetch` followed by a fast-forward only: never a reset, stash, branch switch or discarded local edit. Any refusal (offline, local edits, another branch, a diverged `main`) starts the server on its current code.
- Changed dependency pins are installed before the fast-forward, and only when no other server of the checkout is running. Otherwise the update waits for a later start.
- Running servers are never stopped and keep the code they started with.
- Every running server records its commit, branch, start time, PID and client session. `python -m maxsurf_mcp.autoupdate` lists them as `current` or `OUTDATED`.
- `MAXSURF_MCP_AUTO_UPDATE=0` turns the update off for one client.

See [Git checkout installations: automatic updates](INSTALL.md#git-checkout-installations-automatic-updates).

### Fixed

- `open_design` refuses a Git LFS pointer file before any COM call.

The tool surface and schemas are unchanged from 0.2.0-rc.6 (144 default tools). Live Maxsurf checks were not repeated for this release; the recorded evidence is for 0.2.0-rc.6, see [Verification](VERIFICATION.md).

## Verification and limitations

The original release candidate exposed 90 default tools. The expanded source
checkout exposes 144 default tools and seven opt-in diagnostics, including
installed API discovery, validated native invocation and bounded collection
enumeration. Older archives do not automatically acquire these changes.
Recorded offline and native checks are described in [Verification](VERIFICATION.md);
remaining engineering acceptance gates are listed in [Release status](RELEASE_STATUS.md).
A successful installation or connection is not an engineering validation.

Native persistence, consistent cross-program engineering inputs, complete damaged stability, broader Motions/structural acceptance and clean-user client installation still have open gates. See those reports before evaluating the package.

## License

**MIT**. Copyright (c) 2026 Ismail Hakki Celik. Third-party licenses and Bentley entitlements remain separate.

## Distribution

Attach the three assets and the generated `SHA256SUMS-*.json`. Bentley binaries, manuals, sample models, private designs and training material are excluded.

[License](../LICENSE) · [Third-party notices](../THIRD_PARTY_NOTICES.md) · [Security](../SECURITY.md)
