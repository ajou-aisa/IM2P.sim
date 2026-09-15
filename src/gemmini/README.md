# Pinned Gemmini sources

Immutable provenance snapshots from Gemmini `25809f78323a729ef76fb68f3cedd8a24da2942b`, selected by Chipyard `69eba860a352343e4ac6b6df0f3638a79a86ec78`.
Generate with `uv run scripts/gemmini_vendor.py`; verify with `uv run scripts/gemmini_vendor.py --verify`.

`vendor-manifest.json` records each upstream path, Git blob, snapshot SHA256, dependencies, patches, and compile inclusion. Snapshots are immutable. Run `uv run scripts/gemmini_vendor.py --overlay NEW_DIR` to apply the recorded packed-input controller patch to separate copies. Supply that output through `scripts/gemmini_build.py`'s source override, which explicitly replaces the imported Gemmini build's `Compile / unmanagedSources` through SBT. Full `Scratchpad.scala` is provenance-only because its SoC wrapper pulls Rocket/TL DMA; standalone integration must extract the listed `ScratchpadBank` boundary without compiling both copies.
