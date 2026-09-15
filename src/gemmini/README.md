# Pinned Gemmini sources

Immutable provenance snapshots from Gemmini `25809f78323a729ef76fb68f3cedd8a24da2942b`, selected by Chipyard `69eba860a352343e4ac6b6df0f3638a79a86ec78`.
Generate with `uv run scripts/gemmini_vendor.py`; verify with `uv run scripts/gemmini_vendor.py --verify`.

`vendor-manifest.json` records each upstream path, Git blob, snapshot SHA256, dependencies, patches, and compile inclusion. No patch is currently applied. Full `Scratchpad.scala` is provenance-only because its SoC wrapper pulls Rocket/TL DMA; standalone integration must extract the listed `ScratchpadBank` boundary without compiling both copies.
