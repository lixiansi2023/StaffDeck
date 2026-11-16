# ADR-0001 · F02 非工程招标点检 SOP 的构建方式

- 状态：Accepted
- 日期：2026-11-16
- 关联材料：`input/非工程类采购规则.xlsx`、`input/用例A_F02非工程招标/`、`input/agent_prompt.md`

## 背景与目标

为「非工程招标采购文件呈批表（F02）」建立一份 SOP，对一次呈批的 5 份附件（OA 呈批表 + 采购方案 + 决策纪要 + 招标文件 + 合同模板）按规则表 8 个审核要素逐项点检，产出 `agent_prompt.md` 约定的结构化 JSON（`request_id / form_code / items[] / conclusion / condition / summary`），交给外部 MCP 工具落库并生成 HTML 报告。

本项目文档约定为 single-context layout（`docs/agents/domain.md`）；本仓当前无 `CONTEXT.md`、无既有 ADR，故本文件是首篇 ADR，同时承担 HLD 职责。后续如出现与本文冲突的 ADR，须显式声明冲突，不静默覆盖。

## 架构决策

| 编号 | 决策 | 理由 | 否决的备选 |
|---|---|---|---|
| D1 | 规则以知识库为权威来源（方案 B）：规则正文导入知识库，节点 `capability_refs.knowledge_base_ids` 引用，节点 instruction 只写「按规则库判定」+ 数据锚点 | 规则版本化、改规则不改 SOP；`rule_lib_version` 可由知识库版本带出 | A：规则写死在 instruction（改规则要发版）；C：新建 `rule_lib` 表 + CRUD + MCP 工具（成本高、与现有知识库能力重复） |
| D2 | `save_audit_result` / `render_audit_report` 视为外部能力，不写实现代码；经 MCP server 注册/同步为 `tools` 行，运行时走 `mcp__staffdeck__tool_invoke` 代理 | 工具已由外部 MCP server（`127.0.0.1:8100/mcp`）提供；平台统一走代理，引擎只挂一个 staffdeck capability server | 在平台内实现这两个工具（重复建设）、给引擎单独挂外部 MCP server（与 `worker.py` 的单代理模型冲突） |
| D3 | SOP 基于 `backend/sop_exports/merged_procurement_compliance_flow.json` 裁剪为 12 节点，而非空白新建 | 合并 SOP 已覆盖同类节点（附件点验、文本/保证金/合同/评分/异议澄清、结论汇总），命名和判定口径可直接继承 | 从 `createBlankSkillCard()` 起搭（易遗漏 `slot_filling_policy`、终止节点等约定） |
| D4 | 预算策略 = 提升 agent `harness_max_actions` 到 64 + 文件读取集中到 n0 节点，单节点只做「比对 + submit_step_result」 | 32 次预算是单 turn 内所有 SOP 节点共享（`turn_coordinator.py:1043-1208`）；集中读取后约 15 次，留余量给 steer 重试 | 每节点省一次调用（踩线，≈23/32，无重试余量）；依赖跨 turn 续跑兜底（体验割裂） |
| D5 | level 三值 `风险/关注/通过` 为唯一口径；Excel 的「否决」统一映射为 `风险` 并在 conclusion 体现 | `agent_prompt.md:26` 的硬约束；报告技能另用 `通过/预警/关注/不通过`，两者在落库层归一 | 混用两套词（下游统计不一致） |

## 组件边界

```
规则源 xlsx ──导入──> 知识库(规则, 待建) ──knowledge_base_ids──> SOP 节点
F02 附件 ──n0 集中读取/落槽──> 槽位 source_docs ──> 8 个要素节点(instruction 比对 + submit_step_result)
外部 MCP server(8100) ──sync──> tools 表 ──tool_ids / call_tool:audit_report.*──> n10/n11
n10 save_audit_result <── JSON 契约(agent_prompt.md) ── n9 汇总
n11 render_audit_report ──> report_url ──> reply_fragment
```

- 规则（知识库）：只存「判定口径」，不存具体项目数据。
- SOP（SkillCard）：只编排流程与比对动作，不实现工具。
- 工具（外部 MCP）：只负责落库与渲染，不判定合规。
- 代理（`mcp__staffdeck__tool_invoke`）：平台侧唯一出网口，外部工具无独立引擎前缀。

### 规则知识库的落地步骤（原「使用说明」，从种子文档剥离）

1. `docs/seeds/规则库_F02非工程招标点检.md` 是纯知识库文档：每条规则一个二级节、标题带要素名与检索关键词、条目自包含，不含导入操作指引（避免污染检索）；「附件角色与识别方法」节明确 A00–A04 是逻辑角色而非固定文件名，须读内容并结合文件名判定。用平台真实分节逻辑验证：11 节（文档名 + 附件角色 + 总则 + 8 条规则），各节均小于 900 字符 evidence chunk 上限。
2. `backend/scripts/import_f02_rules_kb.py` 一步完成：建库（按名字幂等）→ 导入 md（`create_ingest_job` + `_run_ingest_job`，无 LLM 配置亦可）→ 绑定知识库到 agent（保留既有绑定、继承 `agent_private` provenance）→ 回填 `n1`–`n8` 的 `capability_refs.knowledge_base_ids` → 重发布 SOP。`--dry-run` 只预览。
3. **实跑结果（2026-11-16）**：KB `kb_5fdcb2f44ee049fd`（`sop_specific/active`）、文档 `kdoc_b94c76f47aa34e6a`（`ready`、22 chunks / 4 buckets）、已绑定招采数字员工；SOP 8 个检查节点均已回填 `knowledge_base_ids` 与 `required_knowledge_base_ids`，SOP 已重发布（`published`）。

### 跨员工可见性：SOP「生效」需写满 5 张关联表（复用的坑）

对另一个员工（如小明 `agent_327b459bcfd84323`）而言，API `publish_skill` 只保证目标员工自己的分支可见，SOP 不会自动出现在该员工的工作流里。要让 SOP 在指定员工下生效，必须写全 5 张表（范式见 `backend/scripts/copy_sop_to_xiaoming.py`）：
`skills(published)` + `skill_versions(published)` + `agent_resource_bindings(active)` + `agent_skill_branches(active/synced)` + `agent_skill_branch_versions(active/synced)`。
- 只建 `skills` / `skill_versions` 而无后三张关联行 = 「导入了但不生效」，本轮首次即栽在此。
- 导入脚本 `backend/scripts/import_f02_sop_to_xiaoming.py`（增量、幂等）已按此范式把 F02 SOP 完整落到小明名下，可见性校验 4 项全绿。
- 绑定写入一律增量（仅 INSERT/UPDATE 目标行），不做全量替换——避免误删 `status='deleted'` 的历史绑定行。

## 已核实的环境事实（2026-11-16，来自本地库只读查询）

- 租户：`tenant_demo`；目标 agent：`agent_77affcb6b0de4e11`（招采数字员工）。
- 两个审计 MCP server 指向同一地址，工具名重复，本 SOP 选用 `audit_report.*` 一组：
  - `tool_6bcf3633f3bb40ab` = `audit_report.save_audit_result`
  - `tool_d8d13de114f34657` = `audit_report.render_audit_report`
  - （重复组：`tool_32cdd0884a194beb` / `tool_2398f672ed7b45a4`，名 `shen_he_bao_gao.*`，不用）
- 现有知识库中无「非工程类采购规则」专库；D1 要求新建并导入规则，见规则种子文件。
- 发布版 `merged_procurement_compliance_flow`（`skill_id`）可作裁剪基线。

## 预算与容量

- 常量：`MAX_TOOL_ACTIONS_PER_TURN = 32`，硬上限 100（`agent_loop.py:81-82`）。
- 实际作用域：一个用户 turn 内 `remaining_actions = max_actions` 只取一次，多个 SOP 节点在同一 turn 顺序执行并共享扣减（`turn_coordinator.py:1043-1208`）；`submit_step_result` 也计 1 次（`control.py:191`）。
- 目标值：agent `harness_max_actions = 64`。
- 估算：12 节点，n0 一次读全量 + 每检查节点 1 次 submit + n10/n11 各含 capability_describe/tool_invoke/submit，合计约 15 次，远低于 64。
- 兜底：预算耗尽不失败，标记 `action_budget → queued` 并保存 `step_id + slots`（`turn_coordinator.py:1266-1276`），配合 `interruption_policy.resume_from_last_state` 续跑。

## 落地补充（2026-11-16 实跑发现）

- **绑定要求**：SOP 节点 `capability_refs.tool_ids` 引用的工具，必须在目标员工的 `agent_resource_bindings` 中且带 `agent_private` provenance（`scope: agent_private` 等）。仅新增 active 绑定但 metadata 为空时，`CompositionCompiler.visible_resources` 不含该工具，发布报 `SlotNotBound`。创建脚本 `backend/scripts/create_f02_review_sop.py` 已在发布前自动补齐绑定与 provenance。
- **删行语义**：`update_agent_resources` 是全量替换，重跑时须带上全部既有 active/inactive 绑定（跳过 `deleted`），否则会误删。
- **执行顺序**：预算调整须放在发布之前——发布失败抛 `SystemExit` 会跳过其后的步骤。
- **幂等**：脚本用 `update_skill` 处理已存在的 `skill_id`，可重复运行。
- **实跑结果**：SOP 已发布（`agent_skill_branches` 中该 agent 分支 `status=active`，内容为 12 节点/11 边、`n0_collect`→`n11_report`），`harness_max_actions=64`，两工具绑定 active。脚本已加「内容一致则跳过 update」保护，避免重跑无谓升版本。

### D6：禁止把附件正文写入槽位（路径引用模式）

实跑 `SOP_RESULT_REPAIR_EXHAUSTED` 停在 n0 的根因：n0 原要求把 5 份附件全文写进 `source_docs` 槽位，模型在 `submit_step_result` 参数里塞约 11K 字符的巨型 JSON，拼接出错（`Extra data: line 1 column 10998`）。`validate_result`（`structured_output.py:94`）解析失败后误报 `'status' is a required property`（实际参数里有 status），触发结果专门修正，同样因超长载荷 2 次耗尽而失败；且 submit 从未成功，`slots_json={}`，「继续」重启仍从 n0 开始形成死循环。此前单 turn 上下文溢出（message 累积到 50、token 超限）也是同一根源。

决策（D6）：
- n0 只读正文做角色判定，读完即弃；`attachment_role_map` 存「文件名→角色→路径」，`source_docs` 只存角色到 `workspace_path` 的**路径字符串**，绝不存正文。
- n2–n8 从 `source_docs` 取路径，用 `read_file` 按需读取对应文件再判定（这些节点的 `allowed_actions` 已加 `call_tool:read_file`）。
- `response_rules` 新增全局约束「禁止把文件正文写入任何槽位或 submit_step_result 参数」。
- 已在招采数字员工（分支 v1.7.0）与小明（v1.0.0）两侧重新发布并校验：n0 路径引用/禁写正文、n2 read_file 均生效。

## 风险与验收

- 风险：规则知识库未建则 D1 不生效（当前节点 instruction 已内联口径，可先跑通）；两个 MCP server 同名工具需在 `tool_ids` 上精确区分，不能只填名字。
- 验收基准（F02 用例推演）：8 项 = 2 风险（R02 采购方式不一致、R04 资格条件含地域限制/指定品牌）+ 5 关注（R03/R05/R06/R07/R08 命中项）+ 1 通过（R01 附件齐备）；`summary` 的 X/Y/Z 必须与 items 统计一致。R01、R02、R04 在规则表中「判定结论的影响」分别为关注/风险/风险，风险级取规则表口径。
- 校验：`SkillCard.model_validate(content)` 通过（extra=forbid）；能力引用的 `tool_ids` 必须来自 `GET /api/enterprise/tools` 的真实 id。
