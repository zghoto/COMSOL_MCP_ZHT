# COMSOL_MCP_ZHT

[简体中文](README_CN.md)

This is a personal fork of COMSOL Multiphysics MCP for learning and personal use. `main` preserves the upstream baseline, and `zht` contains the changes below. Thanks to the original authors and contributors. [Upstream repository](https://github.com/wjc9011/COMSOL_Multiphysics_MCP)

## Changes on `zht`

- **Desktop sync.** MCP and COMSOL Desktop can work on the same model by tag when connected to the same COMSOL Multiphysics Server. Modeling changes made through MCP appear in the Desktop model tree. [COMSOLPilot workflow reference](https://github.com/waverlose/COMSOLPilot/tree/7c79c78ea8bb92aec6e5b07c4877b169dd9cffb8)
- **Geometry and mesh.** Added edge and domain queries and mesh feature creation. Expanded boundary inspection and improved mesh quality reporting.
- **Studies and results.** Added physics selection per study step, result statistics, and plot creation. Improved parametric sweeps, solver diagnostics, numerical evaluation, and plot image export. [Physics-aware fork reference](https://github.com/juijunnarkar/COMSOL_Multiphysics_MCP/tree/4b0a0afc5db1ad9befcde8e623987d62a985bba2)
- **PDF search and indexing.** Fixed a Windows PDF search startup hang and incorrect module counts in large documentation indexes.
