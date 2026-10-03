# Pinned Gemmini sources

Immutable provenance snapshots from Gemmini `25809f78323a729ef76fb68f3cedd8a24da2942b`, selected by Chipyard `69eba860a352343e4ac6b6df0f3638a79a86ec78`.
Generate with `uv run scripts/gemmini_vendor.py`; verify with `uv run scripts/gemmini_vendor.py --verify`.

`vendor-manifest.json` records each upstream path, Git blob, snapshot SHA256, dependencies, patches, and compile inclusion. Snapshots are immutable. Run `uv run scripts/gemmini_vendor.py --overlay NEW_DIR` to apply the ordered packed-input and loop-reset patches to separate copies. `scripts/gemmini_build.py` supplies `-Dim2p.gemmini.overlay=NEW_DIR` to the single `build.sbt`; it replaces exactly four imported Gemmini sources. The build declares `scuCore` (no upstream or host dependency), `gemminiIntegration`, integrated standalone `root`, and test-only lower-level `diagnostics` source sets (no alternative standalone top). `control/build.sbt` is no longer a separate build. Full `Scratchpad.scala` is provenance-only because its SoC wrapper pulls Rocket/TL DMA; standalone integration must extract the listed `ScratchpadBank` boundary without compiling both copies.
