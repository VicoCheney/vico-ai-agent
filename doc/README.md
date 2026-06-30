# doc/ — Vico 核心文档索引

本目录存放 Vico AI Agent 的核心设计文档。

## 文档列表

| 文件 | 类型 | 说明 |
|------|------|------|
| [architecture.md](./architecture.md) | Markdown | **主文档**：当前代码实现对应的架构、工具调度、Skill 机制、代码索引和 ADR |
| [agent-flow.mmd](./agent-flow.mmd) | Mermaid | Agent Loop、工具调度与 Skill 懒加载流程图（flowchart） |
| [agent-sequence.mmd](./agent-sequence.mmd) | Mermaid | 以"电脑体检"为例的当前执行时序图（sequence diagram） |

## 快速阅读路径

### 想了解"大脑是怎么工作的"
→ 阅读 [architecture.md](./architecture.md)

### 想看清楚"每一步发生了什么"
→ 查看 [agent-flow.mmd](./agent-flow.mmd) 和 [agent-sequence.mmd](./agent-sequence.mmd)

### 想知道"为什么这样设计"
→ 查看 [architecture.md#设计决策记录](./architecture.md#设计决策记录adr) 中的 ADR 部分

## 核心思想一句话总结

> **提示词规划 + 批量执行**：复杂任务由同一个 Agent Loop 通过 `<plan>` 文本先规划，
> 再让模型在同一轮或后续轮次批量发出 structured tool calls；
> Skill 正文按需从 `.vico/skills` 懒加载，避免常驻上下文膨胀。
