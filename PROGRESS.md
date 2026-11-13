# 开发进度（断点续做看这里）

> 任何新会话接手时：先读本文件，再读 docs/DESIGN.md。每完成一步就更新本文件并 git commit。

## 状态
- [x] M0 仓库初始化、设计文档
- [ ] M1 核心模块：manifest / redact / gitutil / adapter 基类
- [ ] M2 Claude Code adapter：capture + materialize
- [ ] M3 CLI：init / capture / snapshot / log / diff / doctor / materialize / export / import
- [ ] M4 测试 + 端到端验证（两台"机器"模拟迁移）
- [ ] M5 文档收尾（README、扩展指南）

## 备注
- 本仓库目前只在本地容器里，没有远端。容器回收后会丢失，需要用户提供 GitHub 仓库后 push。
