# 采购合规校验报告：数据契约

本文件是 `stdin` 输入的完整字段说明，供 SOP 作者与模型对照。字段契约同时内联在
`SKILL.md` 中，因此本文件是可选的阅读补充，不构成运行时依赖。

## 输入

`run_skill_script` 的 `arguments.stdin` 是一个**字符串**，其内容为单个 JSON 对象。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `output_path` | string | 是 | 相对 workspace 的输出路径，必须以 `results/` 开头、以 `.html` 结尾，禁止 `..` |
| `project` | string | 否 | 协审流程项目名；缺省为「采购合规校验」，用于 `<title>` 与 `<h1>` |
| `summary` | string | 否 | 汇总说明，缺省为空 |
| `items` | array | 否 | 校验明细，每项含 `no` / `element` / `rule` / `result` |
| `facts` | array[string] | 否 | 关键数据清单，逐条渲染为 `<li>` |
| `suggestion` | string | 否 | 整改建议，缺省为「无」 |

`items[].result` 建议使用 `通过` / `预警` / `关注` / `不通过`；后三者渲染为红色
warning 样式，其余渲染为绿色 pass 样式。

所有动态值在写入模板前都会经过 `html.escape(..., quote=True)`，因此数据中包含
`"`、`<`、`>`、`&` 是安全的，不需要调用方预先转义。

## 输出

脚本把渲染结果写入 `output_path`，stdout 只返回一个简短 JSON：

- 成功：`{"ok":true,"path":"results/xxx.html","size":1234}`
- 失败：`{"ok":false,"error":"原因"}`，进程退出码非 0

stdout 不含 HTML 正文（`run_skill_script` 的 `max_output_bytes` 默认 64 KiB）。

## 完整示例

```json
{
  "output_path": "results/采购方式合规校验报告.html",
  "project": "采购方式合规校验",
  "summary": "协审流程项目「采购方式合规校验」已完成：2 项检查中 1 项通过、1 项预警（采购方式填写与决策纪要决议不一致），建议修正呈批表采购方式字段后重新提交。",
  "items": [
    {"no": 1, "element": "采购方式一致性校验", "rule": "比对呈批表填写的采购方式与采购决策纪要载明的采购方式，二者一致则通过，不一致则预警", "result": "预警"},
    {"no": 2, "element": "招标限额与预算金额校验", "rule": "比对招标最高限价与预算金额，招标限额不高于预算金额则通过，高于则预警", "result": "通过"}
  ],
  "facts": [
    "呈批表填写采购方式：竞争性磋商",
    "采购决策纪要决议采购方式：公开招标",
    "项目预算金额：4,800,000 元",
    "项目招标最高限价：4,600,000 元"
  ],
  "suggestion": "请将 OA 呈批表中的采购方式修正为「公开招标」，确保与招采专题会议审议通过的决议保持一致，修正后可重新提交合规校验。"
}
```

## 占位符对照

| 模板占位符 | 数据来源 |
| --- | --- |
| `{{TITLE}}` | `project` + 「报告」 |
| `{{SUMMARY}}` | `summary` |
| `{{ROWS}}` | `items` 生成的 `<tr>` 序列 |
| `{{FACTS}}` | `facts` 生成的 `<li>` 序列 |
| `{{SUGGESTION}}` | `suggestion` |
