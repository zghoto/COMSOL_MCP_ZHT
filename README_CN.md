# COMSOL_MCP_ZHT

[English](README.md)

本仓库是 COMSOL Multiphysics MCP 的个人 Fork，仅用于个人学习和自用。`main` 保留上游基线，以下改动位于 `zht` 分支。感谢上游作者和贡献者。 [上游仓库](https://github.com/wjc9011/COMSOL_Multiphysics_MCP)

## `zht` 分支的改动

- **Desktop 同步。** MCP 和 COMSOL Desktop 连接到同一个 COMSOL Multiphysics Server 后，可以按模型标签操作同一个模型。MCP 的建模改动会显示在 Desktop 模型树里。 [COMSOLPilot 流程参考](https://github.com/waverlose/COMSOLPilot/tree/7c79c78ea8bb92aec6e5b07c4877b169dd9cffb8)
- **几何与网格。** 新增边、域查询和网格特征创建，补充边界信息并改进网格质量统计。
- **研究与结果。** 新增研究步物理场选择、结果统计和绘图工具，改进参数扫描、求解诊断、数值结果读取和图组导出。 [物理感知分支参考](https://github.com/juijunnarkar/COMSOL_Multiphysics_MCP/tree/4b0a0afc5db1ad9befcde8e623987d62a985bba2)
- **PDF 检索与索引。** 修复 Windows 下 PDF 检索启动卡住的问题，以及大索引的模块计数错误。
