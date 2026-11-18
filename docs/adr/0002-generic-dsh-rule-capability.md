# ADR-0002 · 平台级通用规则能力：DSH 生成规则文本与逐条判定

- 状态：Proposed
- 日期：2026-11-17
- 关联：ADR-0001（D1 冲突，本文显式声明替代）、`docs/seeds/规则_F02_R*.md`、`input/Deepseek Harnnes 执行组件.md`
- 首个用例：F02 非工程招标点检；**F02 只是示例，本能力必须与表单解耦**

## 1. 背景与目标

ADR-0001 为 F02 做了一套专用方案：规则正文导入知识库，8 个 SOP 节点各按 instruction 自行判定。该方案能跑通，但每接一个新表单都要重做「规则文档 + 知识库 + SOP 节点 + 提示词」，规则无法复用、无法版本化、无法审计，判定质量完全取决于节点提示词。

本 ADR 提出平台级通用规则能力。核心约束来自需求方：

1. **规则文本本身由 DSH 生成**——从制度文件、规则表、自然语言需求中生成结构化规则；
2. **每条规则由 DSH 生成该条判定结果**——规则执行不是代码算条件，而是逐条驱动一次有界的 DSH 生成任务。

目标：接入新表单 = 提供规则源与事实映射 + 挂接规则集，而不是重写一套 SOP。

## 2. 与 ADR-0001 的冲突声明

ADR-0001 的 D1 明确「规则以知识库为权威来源（方案 B）」，并在否决栏写下「新建 `rule_lib` 表 + CRUD + MCP 工具（成本高、与现有知识库能力重复）」。本 ADR 的通用规则集表与该决策冲突，**按 ADR-0001 自身约定显式声明替代**：

- 本文接管：规则的结构化存储、版本、生成、逐条执行、聚合与审计；
- ADR-0001 D1 继续有效于其原范围：纯文本规则资料的检索召回、SOP 节点的 `knowledge_base_ids` 引用；
- ADR-0001 D2/D3/D5/D6/D7 不受影响，继续有效。

替代理由：通用能力要求规则本身成为可管理的一等实体（版本、状态、复用、审计），这是"文档放知识库"无法提供的；且规则文本与判定结果都由 DSH 生成后，知识库退居为规则生成与执行时的**检索来源**，不再是唯一权威。

## 3. 架构决策

| 编号 | 决策 | 理由 | 否决的备选 |
|---|---|---|---|
| D1 | 新增 `rule_sets` / `rules` / `rule_versions` / `rule_evaluations` 四张表，规则成为一等实体 | 版本化、复用、审计、跨表单；知识库只作检索来源 | 继续沿用 ADR-0001 D1 纯知识库方案（无法满足通用与审计） |
| D2 | 两条生成链路统一走 DSH：`RULE_AUTHOR`（生成规则文本）与 `RULE_EVALUATE`（生成逐条判定） | 需求方明确约束；规则是自然语言语义，代码求值覆盖不了 | 确定性条件树 / Drools 类规则引擎作为主线 |
| D3 | 规则规格映射到现有 `TaskRequirement`，经 `TaskAgent.run` 调用 DSH，不改 DSH 的 TS 插件 | `TaskRequirement` 已有 `goal/requirements/completion_criteria/required_knowledge_base_ids/attachments/expected_slots`，足够承载任务规格；改 TS 插件需重跑 `fetch-and-build.sh`，成本高 | 在 `harness-engine/` 写新插件 |
| D4 | 每条规则一次短生命周期 DSH 会话（独立 `per_rule`），支持 `batch` 模式批量合并以省成本 | 判定隔离、可复现、单条失败不影响其它 | 递归调用当前 DSH session（会话嵌套、上下文污染） |
| D5 | 附件只传 `workspace_path`，不传正文；生成结果强制 JSON Schema 校验 | 沿用 ADR-0001 D6/D7 的载荷溢出教训 | 把附件正文塞进规则上下文 |
| D6 | 结论聚合（X/Y/Z 统计、conclusion/summary）由代码完成，不由 DSH 生成 | 当前规则反复强调"必须与 items 一致"，属易错点；代码可保证 | 交给 DSH 自行统计 |
| D7 | 规则生成后进入 `draft`，必须人工复核才能 `published`；SOP 节点按版本 pin 规则集 | 生成式规则有误报/幻觉风险；历史结论需可复现 | 生成即生效、始终取最新版本 |

## 4. 总体架构

```
规则源(Excel/制度文本/需求描述)
    │  RULE_AUTHOR：DSH 生成
    ▼
rule_sets + rules(draft) ──人工复核──> rule_versions(published, 版本不可变)
    │                                        │
    │                                        ├─ AgentResourceBinding(rule_set) → 员工可见
    │                                        └─ SOP 节点 capability_refs（pin 版本）
    ▼
RULE_EVALUATE：逐条 DSH 任务（读附件路径 + 检索规则专属 KB）
    │  输出 level/opinion/evidence/rule_id，JSON Schema 校验
    ▼
代码聚合 conclusion/summary + rule_evaluations 审计落库
    │
    ▼
外部 MCP：save_audit_result / render_audit_report（沿用 ADR-0001 D2）
```

组件边界：

- 规则表只存「规则定义与版本」，不存具体项目数据；
- DSH 只负责按规则生成文本或判定，不持有状态；
- 代码负责编排、校验、聚合、审计；
- 知识库负责为生成任务提供受限检索上下文（`query_type=policy_check`）。

## 5. 领域模型

新增四表，遵循仓库范式：`id(new_id 前缀)` + `tenant_id`(index) + `status` + `metadata_json` + `created_at/updated_at`；需要版本的表配套 `*_versions`。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `rule_sets` | `code / name / description / form_code(nullable) / status / current_version / capability_scope` | 表单无关；`form_code` 仅作检索标签，不参与判定逻辑；**不设固定事实结构** |
| `rules` | `rule_set_id / rule_id / seq / type / criteria_md / inputs_json / kb_refs_json / author_dsh_spec_json / eval_dsh_spec_json / decision_json / enabled` | `type` = `author_generated` / `manual`；一条规则一份任务规格 |
| `rule_versions` | `rule_set_id / version / snapshot_json / immutable / published_by / published_at` | 生成后冻结的不可变快照，保证历史可复现 |
| `rule_evaluations` | `request_id / rule_set_version / rule_id / input_ref_json / output_json / evaluator / prompt_version / evidence_refs_json / status / latency_ms / created_at` | 逐条判定审计；`input_ref_json` 只存用户提交材料的引用/摘要，不存正文 |

复用 `AgentResourceBinding(resource_type='rule_set', resource_id=<rule_set.id>)` 完成员工/agent 挂接，沿用 ADR-0001「5 张关联表写满才生效」的经验。

## 6. 规则配置 Schema

一条规则同时携带"生成任务规格"与"判定任务规格"：

```json
{
  "rule_id": "ZC-F02-R04",
  "seq": "04",
  "element_tag": "资格要求",
  "type": "author_generated",
  "criteria_md": "逐条比对资格条件与采购需求的关联性……",
  "inputs": {
    "source": "user_submission",
    "extraction_hints": [
      "资格条件：从提交文本中找出资格要求条款原文",
      "采购需求：从提交文本中找出项目规模与需求描述"
    ],
    "inline_text_allowed": true,
    "attachment_paths_from_user": true
  },
  "kb_refs": ["kb_5fdcb2f44ee049fd"],
  "author_dsh_spec": {
    "source_types": ["xlsx_rule_table", "policy_doc", "free_text"],
    "output_schema": "RuleAuthoringContract/v1",
    "review_required": true
  },
  "eval_dsh_spec": {
    "goal": "按规则判定本条要素，输出一条结构化结论",
    "required_capability_names": ["sandbox.read_file"],
    "max_actions": 8,
    "output_schema": "RuleDecisionContract/v1",
    "retry": 2
  },
  "decision": {
    "levels": ["风险", "关注", "通过"],
    "opinion_template": "命中第 {clause} 条：{finding}",
    "evidence_policy": "一句话摘要，禁止抄录正文"
  }
}
```

**输入来源统一为用户提交**：`inputs.source = user_submission` 表示判定材料来自用户在本次任务中直接提交的文本或上传的文件，不存在平台预置的 A00–A04 角色体系或固定表单字段。`extraction_hints` 只描述"要从提交文本里找出什么"，由 DSH 在生成任务中自行抽取事实；规则集不声明、也不依赖任何预定义字段路径。用户粘贴的正文可经 `inline_text_allowed` 直接进入任务，上传文件则给出路径由 DSH 用 `read_file` 自取。

`RuleDecisionContract/v1`：`{ rule_id, level, opinion, evidence, clause_refs[] }`，必填且 `level` 只能取三值。

## 7. DSH 调用契约

### 7.1 RULE_AUTHOR（规则文本生成）

- 输入：**用户提交的规则源文本或文件**（粘贴文本走 `current_user_message`，上传文件走 `attachments` 路径）、目标语境、输出契约；
- 映射到 `TaskRequirement`：`goal` = 生成规则集，`requirements` = 逐条列出规则表字段要求，`completion_criteria` = 每条规则必须含 id/要素名/判定口径/级别映射/数据锚点/检索关键词，`expected_slots` = `rules_draft`；
- 输出：规则数组 + 每条的 `criteria_md`、`inputs`、`kb_refs`、`decision` 草案；
- 落库为 `draft`，人工复核通过后生成 `rule_versions` 快照并发布。

### 7.2 RULE_EVALUATE（逐条判定生成）

- 输入：冻结的规则快照 + **用户本次提交的待审文本/文件** + 该规则专属 KB；
- 映射到 `TaskRequirement`：`goal` = 判定本条要素，`requirements` = 规则正文 + `extraction_hints`，
  `current_user_message` = 用户粘贴的正文，`attachments` = 用户上传文件的路径列表，
  `required_knowledge_base_ids` = `kb_refs`，`completion_criteria` = 输出契约，`expected_slots` = `decision`；
- 输出：一条 `RuleDecisionContract/v1` 对象；
- 校验失败按 `retry` 重跑，仍失败则该条标记 `error`，不静默补"通过"。

### 7.3 公共约束

- 调用缝：`TaskAgent.run(requirement, model_config, invoke_tool, ...)`（`bridge/task_agent.py:311`），
  DSH 进程由 `HarnessV3Process` 经 `HarnessClient` 拉起（`bridge/worker.py:176-214`）；
- 会话：每次任务独立短生命周期，通过 `services.session(identity, purpose="capability")` 隔离；
- 材料：用户粘贴的正文作为任务输入直接传入（这是任务输入，不是槽位）；用户上传的文件只给路径让 DSH 用 `read_file` 自取。D6/D7 的"禁写正文"约束针对 SOP 槽位与 `submit_step_result` 载荷，不禁止把用户提交文本作为 DSH 任务输入；
- 工具白名单：判定任务仅开 `read_file` 与必要知识库检索，禁止落库类工具；
- 审计：任务开始/结束记录 `rule_set_version`、`prompt_version`、耗时与输出哈希。

## 8. 生命周期

```
draft ──人工复核──> published(pinned version) ──新版本发布──> archived
  ↑                        │
  └── DSH 生成/编辑 ────────┘
```

- 生成与编辑只产生 `draft`；`published` 不可逆，修改必须升版本；
- SOP 节点在 `metadata` 中 pin `rule_set_version`，不随最新漂移；
- `archived` 仍可审计与回放，不再被新任务引用。

## 9. 平台接入

- **API**：`api/rule_sets.py`（CRUD、生成、复核、发布、评估），schema 放 `app/rules/schema.py`，逻辑放 `app/rules/service.py`，测试放 `backend/tests/test_rule_engine.py`；
- **内部工具**：注册 `rule_engine.generate_rules` / `rule_engine.evaluate` 到 `tools` 表，经 `mcp__staffdeck__tool_invoke` 代理暴露，SOP 节点用 `capability_refs.tool_ids` 引用；
- **SOP 简化**：F02 从 12 节点收敛为「采集附件路径 → 生成规则（首次）/逐条判定 → 聚合 → 落库 → 报告」；保留 n0 的路径引用模式与 D7 的一条一槽；
- **可见性**：按 ADR-0001 的 5 表范式挂接到目标员工，绑定写入一律增量；
- **知识库**：继续存规则源材料与检索片段，`KnowledgeSearchRequest.query_type=policy_check` 作为受限检索入口。

## 10. 落地路线

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 四张表 + schema/service/API + `TaskRequirement` 映射 + 两种 DSH 任务契约 | 单测通过；用 F02 规则表跑通 `RULE_AUTHOR` 生成 draft |
| P1 | `RULE_EVALUATE` 逐条判定 + JSON Schema 校验 + 代码聚合 + 审计 | F02 用例 8 条逐条生成，与 ADR-0001 基准（2 风险/5 关注/1 通过）比对 |
| P2 | 版本 pin、人工复核流程、`batch` 模式、AgentResourceBinding 挂接、SOP 收敛 | 新表单（F03）只配置规则源即可跑通，不改 SOP 代码 |
| P3 | 规则生成质量回归集、误报监控、规则复用库 | 规则变更可回放历史判定 |

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 生成规则出现幻觉/漏项 | 强制 `draft` + 人工复核；`completion_criteria` 校验必备字段 |
| 用户提交的规则源或待审文本含提示词注入 | 一律作为数据放入 `requirements` / `current_user_message`，不进 system 指令；工具白名单收敛 |
| 逐条 DSH 会话成本高、延迟大 | `batch` 模式合并低风险规则；重要规则才 `per_rule`；会话池复用进程 |
| DSH 递归调用当前会话 | 独立短生命周期会话，通过 `services.session` 隔离，禁止在 SOP 节点内再起嵌套 |
| 载荷溢出（D6/D7 复发） | 上传文件只传路径；判定结果只回一条对象；evidence 一句话；用户粘贴正文只进任务输入，不回写槽位 |
| 规则版本漂移 | SOP pin `rule_set_version`；审计表记录版本与输出哈希 |

## 12. 验收标准

1. 新建表单不写新 SOP 即可完成规则生成与逐条判定；
2. 8 份 F02 规则文档可作为规则源被 `RULE_AUTHOR` 还原为结构化规则 draft；
3. 每条判定由独立 DSH 任务生成，输出满足 `RuleDecisionContract/v1`；
4. 统计与结论由代码聚合，`X+Y+Z` 恒等，有风险时结论不为「建议通过」；
5. 历史 `rule_evaluations` 可按版本回放，输出一致；
6. ADR-0001 的 D2/D5/D6/D7 约束仍全部满足。
