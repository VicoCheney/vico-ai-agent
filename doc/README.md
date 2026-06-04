# doc/ — Vico 核心文档索引

本目录存放 Vico AI Agent 的核心设计文档。

## 文档列表

| 文件 | 类型 | 说明 |
|------|------|------|
| [architecture.md](./architecture.md) | Markdown | **主文档**：完整的架构设计、决策模型、代码索引和 ADR |
| [agent-flow.mmd](./agent-flow.mmd) | Mermaid | Agent 决策与执行的全流程流程图（flowchart） |
| [agent-sequence.mmd](./agent-sequence.mmd) | Mermaid | 以"电脑体检"为例的完整时序图（sequence diagram） |

## 快速阅读路径

### 想了解"大脑是怎么工作的"
→ 阅读 [architecture.md](./architecture.md)

### 想看清楚"每一步发生了什么"
→ 查看 [agent-flow.mmd](./agent-flow.mmd) 和 [agent-sequence.mmd](./agent-sequence.mmd)

### 想知道"为什么这样设计"
→ 查看 [architecture.md#设计决策记录](./architecture.md#设计决策记录adr) 中的 ADR 部分

## 核心思想一句话总结

> **先规划，再批量执行**：复杂任务先用 Planner（无工具权限）做一次轻量拆解，
> 生成 `[batch]`/`[seq]` 标注的执行计划，再让 Executor 按计划批量发出工具调用，
> 将 LLM round-trips 从 O(N) 降到接近 O(log N)。
