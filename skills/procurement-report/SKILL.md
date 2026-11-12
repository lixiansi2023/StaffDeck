---
name: 采购合规校验报告生成
description: 依据已核对的采购合规校验结论数据，套用包内固定模板渲染 HTML 报告，写入 results 目录并发布为可下载交付物。
---

# 用途

本技能把「校验结论数据」渲染成固定版式的 HTML 报告文件。HTML 模板与渲染脚本都在本包内，
模型只负责提供一份结构化数据 JSON。

【硬性约束】不得自行编写、拼接整份 HTML，不得用 write_file 直接写入报告 HTML。
模板里的 CSS、属性双引号和换行全部由包内文件承载，不经模型输出，避免 JSON 转义破坏调用。

# 【禁止】不要调用 exec_command，不要预先创建目录

本技能**不需要**任何 `exec_command` / `mkdir` / `make_directory` 调用。
渲染脚本内部已经会执行 `output.parent.mkdir(parents=True, exist_ok=True)`，
自动创建 `results/` 目录，模型不要提前建目录、不要检查目录是否存在、
不要用 shell 命令写文件或查看文件。

原因：部分 Windows 部署的 `exec_command` 依赖 PowerShell 可执行文件在系统 PATH 中，
缺失时会返回 `IO_ERROR / FileNotFoundError`（进程根本没启动，回执为 outcome_unknown）。
这类调用对本技能没有任何价值，只会让步骤失败。

本步骤允许的工具调用**只有**以下四类，除此之外一律不要调用：
1. `mcp__staffdeck__general_skill_read` 加载本技能；
2. `mcp__staffdeck__sandbox_execute` 的 `read_file`（按需读附件或包内文件）；
3. `mcp__staffdeck__sandbox_execute` 的 `run_skill_script` 执行渲染；
4. `mcp__staffdeck__sandbox_execute` 的 `publish_artifact` 发布报告。

# 文件工具参数契约（必须先读，参数名写错会直接失败）

本运行时所有文件工具统一用 **`path`**，且都是 `additionalProperties: false`——
多传任何一个字段（例如 `file_path`、`filename`、`encoding`）都会整体校验失败。
`file_path` 是其他框架的参数名，本运行时**禁止使用**。

读用户上传的附件或包内文件：

```json
{
  "tool": "read_file",
  "arguments": {
    "path": "/workspace/attachments/<附件的 workspace_path 原值>"
  }
}
```

- 附件路径以任务给出的 `workspace_path` / `workspace_relative_path` 原值为准，
  不要改写、不要截断、不要自己拼哈希；`/workspace/` 前缀是合法别名，可原样使用。
- `read_file` 只接受 `path`（必填）、`offset`、`max_bytes`、`continuation_token`。
  `truncated: true` 时把返回的 `continuation_token` 与同一个 `path` 原样传回，禁止猜字节偏移。
- 包内文件也用 `read_file` 读，`path` 取第 1 步返回的 `file_paths` 原值。
- `publish_artifact` 的参数同样是 `path`，不是 `file_path`。

遇到 `INVALID_ARGUMENTS` 且错误里有 `missing: path` 或 `extra_forbidden: file_path`，
说明参数名写错了；这类错误不可重试，必须改成 `path` 后重新提交，不要原样重复调用。

# 包内文件

- `templates/report.html`：报告模板，动态位置用 `{{TITLE}}` `{{SUMMARY}}` `{{ROWS}}` `{{FACTS}}` `{{SUGGESTION}}` 占位。
- `scripts/render_report.py`：读取 stdin 的 JSON 数据，转义并渲染，写出 HTML 文件。
- `data-contract.md`：stdin 数据字段契约与本技能的完整说明。

所有路径都是相对技能包根目录，运行时会被物化到 TaskFrame workspace 的
`.harness/skill-packages/<slug>-<digest>/` 下。

# 执行步骤（严格按顺序）

1. 调用 `mcp__staffdeck__general_skill_read` 加载本技能：

   ```json
   {"skill_id":"<capability_describe 返回的 id，不要猜 slug>","query":"生成采购方式合规校验报告"}
   ```

   从返回的 `package_root` 和 `file_paths` 中确认 `scripts/render_report.py` 与
   `templates/report.html` 的实际路径。禁止自己拼接包路径，也不要假设 workspace 根目录有 SKILL.md。

2. 调用 `mcp__staffdeck__sandbox_execute` 执行渲染：

   ```json
   {
     "tool": "run_skill_script",
     "arguments": {
       "script_path": "<第 1 步返回的 file_paths 中 scripts/render_report.py 那一项>",
       "stdin": "<一个 JSON 文本字符串>",
       "timeout_seconds": 60
     }
   }
   ```

   注意 `stdin` 的类型是**字符串**，不是对象：必须把完整 JSON 当作字符串传入，
   内部双引号要写成转义形式。字段见下方数据契约。数据里的中文、引号、尖括号
   由脚本统一做 HTML 转义，不要预先转义，也不要在 JSON 外附加说明文字。

3. 脚本 stdout 返回形如 `{"ok":true,"path":"results/xxx.html","size":N}` 的结果。
   `ok` 不为 true 时读 stderr 修正数据后重试，不要改用 write_file 兜底。
   成功后调用一次 `mcp__staffdeck__sandbox_execute` 发布交付物：

   ```json
   {
     "tool": "publish_artifact",
     "arguments": {
       "path": "<上一步返回的 path>",
       "display_name": "采购方式合规校验报告.html"
     }
   }
   ```

4. 最后按 SOP 要求，必须且只能调用一次 `mcp__staffdeck__submit_step_result`
   提交步骤结果（带 status 与 reply_fragment），然后停止。本技能不替代这一步。

# stdin 数据契约

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| output_path | string | 必填，形如 `results/采购方式合规校验报告.html`，只能是 results 目录下的 .html |
| project | string | 协审流程项目名，如「采购方式合规校验」 |
| summary | string | 一到两句汇总，含通过/预警数量与原因 |
| items | array | 校验明细，每项 `{"no":1,"element":"审核要素","rule":"判断规则","result":"通过"}` |
| facts | array[string] | 关键数据清单 |
| suggestion | string | 整改建议，无则空串 |

最小示例（作为 stdin 字符串的内容）：

```json
{"output_path":"results/report.html","project":"采购方式合规校验","summary":"2 项检查中 1 项通过、1 项预警。","items":[{"no":1,"element":"采购方式一致性","rule":"两者一致则通过","result":"预警"}],"facts":["呈批表采购方式为竞争性磋商"],"suggestion":"请修正为公开招标。"}
```

# 数据纪律

- items / facts / summary / suggestion 只能来自本步骤已核对的真实结论，禁止编造数字、金额与结论。
- result 只能是：通过 / 预警 / 关注 / 不通过。脚本据此选择 pass 或 warning 样式。
- 报告标题由 project 自动补「报告」二字；project 本身不要带「报告」。
- 一份任务只生成一份报告；output_path 固定写在 results 目录下，不要用绝对路径。
