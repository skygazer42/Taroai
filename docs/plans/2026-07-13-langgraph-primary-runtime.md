# LangGraph 主运行时切换计划

> **状态（2026-07-21）：已实施。** 原生 Run 已统一通过 `StateGraph(AgentRuntimeState)` 执行，运行时模式开关和 `legacy / loop_v2` 双轨已移除。本文仅保留为实施记录；当前行为以运行时代码、回归测试和 [CREAO 收口审计](2026-07-21-creao-completion-audit.md) 为准。

**目标：** 使用 `AgentRuntimeState` 作为唯一状态模型，由 LangGraph 负责节点顺序、条件分支和修复循环，移除 `legacy / loop_v2` 运行时双轨。

**边界：** 保留现有策略、工具网关、审批、审计、计费、沙箱和业务检查点实现；本次只替换流程编排层，不改外部 Agent Engine 适配路径。

### 任务 1：锁定主执行契约

**文件：**
- 修改：`tests/api/test_agent_runtime.py`
- 修改：`tests/api/test_domain_store.py`

1. 增加失败测试，确认 `AgentRuntime.execute_run()` 调用编译后的 LangGraph。
2. 确认图状态直接使用 `AgentRuntimeState`，不再维护第二份 `TypedDict` 状态。
3. 确认配置中不再暴露运行时模式开关。

### 任务 2：把循环阶段改为真实节点

**文件：**
- 修改：`apps/api/src/taroai/agent/graph.py`
- 修改：`apps/api/src/taroai/agent/loop.py`
- 修改：`apps/api/src/taroai/agent/state.py`

1. 将初始化与恢复、决策、策略、动作、结果观察、校验、修复、重规划和终止处理拆成节点。
2. 用条件边连接等待、失败、完成和修复回路。
3. 继续通过现有 `AgentCheckpoint` 保存业务状态，保证审批、重试和工作进程重启后的恢复契约不变。

### 任务 3：运行时直接切图

**文件：**
- 修改：`apps/api/src/taroai/agent/runtime.py`
- 修改：`apps/api/src/taroai/agent/__init__.py`
- 修改：`apps/api/src/taroai/config.py`
- 修改：`apps/api/src/taroai/app.py`
- 修改：`apps/api/src/taroai/workers/runner.py`
- 修改：`tests/api/test_chat_threads_api.py`

1. 原生 Run 一律通过编译图执行；外部 Agent Engine 仍走对应适配器。
2. 审批恢复、拒绝、取消和重试统一使用图执行状态。
3. 删除 `agent_runtime_mode` 配置和基于模式的功能判断。

### 任务 4：验证

1. 运行图与运行时目标测试。
2. 运行聊天、恢复和持久化回归测试。
3. 运行静态编译检查与 `git diff --check`。
