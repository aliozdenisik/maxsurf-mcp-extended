# Maxsurf MCP 0.2.0-rc.6

First public release candidate for connecting Codex and Claude Desktop to locally installed Bentley Maxsurf applications.

**Technical preview.** This package does not claim complete native-suite coverage, production engineering approval or vessel/class certification.

## Install

| Client or method | Asset | Instructions |
| --- | --- | --- |
| Claude Desktop extension | `maxsurf-mcp-0.2.0-rc.6-win-x64-py312.mcpb` | [Claude setup](INSTALL.md#claude-desktop-extension) |
| Codex plugin | `maxsurf-mcp-0.2.0-rc.6-codex-win-x64-py312.zip` | [Codex setup](INSTALL.md#codex-plugin) |
| Source through PowerShell | `maxsurf-mcp-0.2.0-rc.6-source.zip` | [Source installation](INSTALL.md#powershell-source-installation) |

Windows x64, CPython 3.12 x64 and separately licensed Bentley applications are required. The extension/plugin bundles include Python dependencies; Python and Bentley software are installed separately.

For Claude, select the MCPB during installation, then select `python.exe` and a design workspace in Configure. For Codex, extract the dedicated Codex ZIP and register its local marketplace. Open only the Bentley module required for the task.

## Native enum resolution

Every tool that takes or reports a Maxsurf enumerated value now resolves it through one module, keyed by the enum type names used in the Maxsurf automation manuals (`msSurfaceType`, `msMarkerType`, `hsMethodType`, …). Numbers come from the live type library; the generated constants are used only when the type library cannot be read.

- Member names are matched case-insensitively and trimmed.
- An unknown member returns one error shape from every tool: `field`, `requested`, `enum_type` and a sorted `available` list for that type only.
- Prefix collisions no longer leak unrelated members into `available` (for example, stringer stiffness members no longer appear in the surface `symmetry` error).
- Successful tool output and the tool schema are unchanged.

Checked against live Maxsurf 26.00.00.433 in Modeler, Stability, Resistance, Motions and Multiframe; see [Verification](VERIFICATION.md).

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
