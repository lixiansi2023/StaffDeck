#!/usr/bin/env python3
"""把已落库的合并 SOP 从数据库导出为本地文件。

产出：
  backend/sop_exports/merged_procurement_compliance_flow.json  —— 完整 content（与库内一致，可移植）
  backend/sop_exports/merged_procurement_compliance_flow.md    —— 可读流程文档

直接读 SQLite（不经由公共 API）。
"""
from __future__ import annotations

import json
import os
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "..", "skill_agent_loop.db")
BRANCH = "agentbranch_agent_327b459bcfd84323_merged_procurement_compliance_flow"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "sop_exports")


def main() -> None:
    c = sqlite3.connect(DB)
    cur = c.cursor()
    cur.execute(
        "SELECT content_json, skill_id, head_version, status, sync_state "
        "FROM agent_skill_branches WHERE id = ?",
        (BRANCH,),
    )
    row = cur.fetchone()
    c.close()
    if not row:
        raise SystemExit("合并分支尚未落库，无法导出")
    content_json, skill_id, version, status, sync_state = row
    content = json.loads(content_json)

    os.makedirs(OUT_DIR, exist_ok=True)
    json_path = os.path.join(OUT_DIR, f"{skill_id}.json")
    md_path = os.path.join(OUT_DIR, f"{skill_id}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    print(f"已写出 JSON: {os.path.abspath(json_path)}")

    # ---- Markdown 渲染 ----
    nodes = content["nodes"]
    edges = content["edges"]
    nmap = {n["node_id"]: n for n in nodes}
    type_cn = {
        "collect_info": "收集信息", "tool_call": "工具调用", "knowledge_query": "知识检索",
        "decision": "判定分支", "response": "回复", "handoff": "转人工",
    }

    lines = []
    lines.append(f"# {content.get('name', skill_id)}")
    lines.append("")
    lines.append(f"- **skill_id**: `{skill_id}`")
    lines.append(f"- **version**: `{version}`（库内状态：`{status}` / `{sync_state}`）")
    lines.append(f"- **business_domain**: {content.get('business_domain', '')}")
    lines.append(f"- **起点**: `{content.get('start_node_id')}`")
    lines.append(f"- **终点**: {', '.join('`' + t + '`' for t in content.get('terminal_node_ids', []))}")
    lines.append("")
    if content.get("description"):
        lines.append("## 概述")
        lines.append("")
        lines.append(content["description"])
        lines.append("")
    if content.get("goal"):
        lines.append("## 目标")
        lines.append("")
        for g in content["goal"]:
            lines.append(f"- {g}")
        lines.append("")
    if content.get("trigger_intents"):
        lines.append("## 触发意图")
        lines.append("")
        lines.append("、".join(content["trigger_intents"]))
        lines.append("")
    if content.get("required_info"):
        lines.append("## 必要信息")
        lines.append("")
        lines.append("、".join(f"`{x}`" for x in content["required_info"]))
        lines.append("")

    # 节点清单（按出现顺序）
    lines.append("## 节点清单")
    lines.append("")
    lines.append("| 节点ID | 类型 | 名称 |")
    lines.append("| --- | --- | --- |")
    for n in nodes:
        lines.append(f"| `{n['node_id']}` | {type_cn.get(n.get('type'), n.get('type'))} | {n.get('name','')} |")
    lines.append("")

    # 流程边（按源节点分组，按生命周期顺序）
    lines.append("## 流程走向（边）")
    lines.append("")
    lines.append("| 从 | 条件 / 标签 | 到 |")
    lines.append("| --- | --- | --- |")
    order = {n["node_id"]: i for i, n in enumerate(nodes)}
    for e in sorted(edges, key=lambda x: (order.get(x["source_node_id"], 0), order.get(x["next_node_id"], 0))):
        cond = e.get("label") or e.get("condition") or ""
        lines.append(f"| `{e['source_node_id']}` | {cond} | `{e['next_node_id']}` |")
    lines.append("")

    # 可读主线（从起点 DFS，标注分支）
    lines.append("## 主线串联")
    lines.append("")
    terminal = set(content.get("terminal_node_ids", []))
    visited = set()

    def walk(nid, depth, branch_label):
        if nid in visited and nid not in terminal:
            lines.append(f"{'  '*depth}- …（已访问 `{nid}`）")
            return
        visited.add(nid)
        node = nmap.get(nid)
        name = node.get("name", nid) if node else nid
        t = type_cn.get(node.get("type"), node.get("type")) if node else ""
        prefix = f"{'  '*depth}- "
        if branch_label:
            prefix += f"_{branch_label}_ "
        lines.append(f"{prefix}`{nid}` **{name}** （{t}）")
        if nid in terminal:
            return
        outs = [e for e in edges if e["source_node_id"] == nid]
        for e in outs:
            lbl = e.get("label") or e.get("condition") or ""
            walk(e["next_node_id"], depth + 1, lbl if len(outs) > 1 else "")

    walk(content["start_node_id"], 0, "")
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"已写出 Markdown: {os.path.abspath(md_path)}")
    print(f"\n节点 {len(nodes)} 个，边 {len(edges)} 条")


if __name__ == "__main__":
    main()
