# Agent 单轮调用次数规范（MAX_TOOL_ACTIONS_PER_TURN）

## 结论

平台（Agent）在**单个对话轮次（turn）内**最多执行的工具/动作调用次数，由命名常量 `MAX_TOOL_ACTIONS_PER_TURN` 定义为 **32 次/轮**，并设置**硬上限 `MAX_TOOL_ACTIONS_PER_TURN_LIMIT = 100` 次/轮（不可绕过）**。

---

## 1. 规范常量（最权威定义）

`backend/app/core/agent_loop.py:81-82`

```python
MAX_TOOL_ACTIONS_PER_TURN = 32
MAX_TOOL_ACTIONS_PER_TURN_LIMIT = 100
```

- `MAX_TOOL_ACTIONS_PER_TURN`（32）：单轮默认上限，即“平台每轮默认最多调用 32 次”的来源。
- `MAX_TOOL_ACTIONS_PER_TURN_LIMIT`（100）：所有配置取值最终都会被 clamp 到 `[1, 100]`，这是不可突破的硬上限。

---

## 2. 默认值 32 在各配置模型中的落地

各配置模型/请求 schema 将 `32` 作为默认值写入，使“32 次”成为可配置项的初始值（用户/后台可调）：

| 层级 | 位置 | 字段 | 默认值 / 约束 |
| --- | --- | --- | --- |
| 租户 UI 配置（DB 模型） | `backend/app/db/models.py:708` | `UIConfig.agent_loop_max_actions` | `= 32` |
| 租户 UI 配置（请求 schema） | `backend/app/api/ui_config.py:69` | `UIConfigUpdateRequest.agent_loop_max_actions` | `Field(default=32, ge=1, le=100)` |
| 智能体画像（DB 模型） | `backend/app/db/models.py:739` | `AgentProfile.harness_max_actions` | `Field(default=32)` |
| 智能体 schema（app 层） | `backend/app/agents/schema.py:21`、`backend/app/public_api/schemas.py:89` | `harness_max_actions` | `Field(default=32, ge=1, le=100)` |
| Harness 组合层会话策略 | `backend/src/staffdeck_harness/contracts/staff.py:39` | `SessionPolicy.max_actions` | `= 32` |
| Harness 分发 schema | `backend/src/staffdeck_harness/distribution/schema.py:15` | `harness_max_actions` | `Field(default=32, ge=1, le=256)` |
| 执行宿主 | `backend/src/staffdeck_harness/bridge/control.py:93` | `ExecutionHost(max_actions=32)` | 内部再 `max(1, min(int(max_actions), 100))` |

> 注：harness 层 `distribution/schema.py` 自身的 `le=256` 只是入参校验上限，真正生效时仍会被 `control.py` 与 `agent_loop.py` 的 clamp 收口到 100。

---

## 3. 取用逻辑与优先级

`backend/app/core/agent_loop.py:954-969` —— `_get_agent_loop_max_actions`：

```python
def _get_agent_loop_max_actions(self, tenant_id, agent_id=None) -> int:
    if not hasattr(self.db, "get"):
        return MAX_TOOL_ACTIONS_PER_TURN            # 无 DB 时回退常量 32
    agent = self.db.get(AgentProfile, agent_id) if agent_id else None
    if agent is not None and (agent.tenant_id != tenant_id or agent.status != "active"):
        agent = None
    if agent is not None:
        value = agent.harness_max_actions
        return max(1, min(int(value), MAX_TOOL_ACTIONS_PER_TURN_LIMIT))   # 智能体优先
    row = self.db.get(UIConfig, tenant_id)
    value = row.agent_loop_max_actions if row else MAX_TOOL_ACTIONS_PER_TURN
    return max(1, min(int(value), MAX_TOOL_ACTIONS_PER_TURN_LIMIT))       # 否则用租户 UI 配置
```

取值优先级：
1. **智能体级** `AgentProfile.harness_max_actions`（优先，前提是该 agent 属于当前租户且状态为 active）；
2. 否则 **租户级** `UIConfig.agent_loop_max_actions`；
3. 都没有则回退到常量 `MAX_TOOL_ACTIONS_PER_TURN`（32）。

无论走哪条路径，最终一律 `clamp` 到 `[1, 100]`（硬上限）。

---

## 4. 测试佐证（契约约束）

- `backend/tests/test_ui_config.py:20-27`：校验 `UIConfig` / `UIConfigUpdateRequest` 默认 `agent_loop_max_actions == 32`，且 `agent_loop_max_actions=101` 会触发 `ValidationError`。
- `backend/tests/test_ui_config_action_budget.py`：
  - `test_agent_loop_action_budget_defaults_to_32_and_accepts_100`：默认 32、接受 100。
  - `test_agent_loop_action_budget_rejects_values_above_100`：拒绝 101。
  - `test_agent_loop_action_budget_clamps_stored_values_to_100`：存储值 999 实际取 100（验证 clamp）。
  - `test_agent_loop_action_budget_prefers_employee_configuration`：智能体 `harness_max_actions=17` 优先于租户配置的 64（验证优先级）。

---

## 5. 一句话总结

“平台每轮最多调用 32 次”的根因是 `backend/app/core/agent_loop.py:81` 的常量 `MAX_TOOL_ACTIONS_PER_TURN = 32`；各处 `default=32` 只是把该默认值写进配置/请求 schema；真正卡死不让超的硬上限是 `MAX_TOOL_ACTIONS_PER_TURN_LIMIT = 100`（在 `agent_loop.py:966/969` 与 `control.py` 双重 clamp）。
