#!/usr/bin/env python3
"""将「批量文件合规校验 SOP」草稿通过公共 API 落库。

端点（main.py:196 挂载在 /api/v1）：
    POST /api/v1/agents/{agent_id}/sops        # 创建草稿 (SOPStructuredCreate)
    POST /api/v1/sops/{sop_id}:publish         # 发布 (SOPPublishRequest, query: agent_id)

鉴权：公共 API 使用 Bearer API Key（形如 sd_...），且当前凭证需具备
      sops:write（创建）与 sops:publish（发布）作用域，并拥有该 agent 的访问权。

用法：
    python scripts/create_validation_sop.py \
        --agent-id agent_xxx \
        --api-key sd_live_xxx \
        --base-url http://localhost:8000/api/v1 \
        --publish

环境变量回退：STAFFDECK_API_KEY / STAFFDECK_BASE_URL / STAFFDECK_AGENT_ID
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# SOP 草稿内容（SkillCard）。每个检查独立节点，第一步门控文件齐备性。
# 把 <...> 占位处的真实校验规则替换为你的业务规则后即可使用。
# ---------------------------------------------------------------------------
SOP_CONTENT: dict[str, Any] = {
    "skill_id": "batch_file_validation",
    "name": "批量文件合规校验 SOP",
    "version": "1.0.0",
    "business_domain": "材料审核",
    "description": "用户上传一批文件后，先校验文件是否齐备，再逐项读取内容做合规检查，最后汇总结论。",
    "capability_scope": "general",
    "step_timeout_seconds": 900,
    "trigger_intents": ["校验上传材料", "材料合规检查", "批量文件校验"],
    "user_utterance_examples": [
        "帮我校验这批文件是否齐全并合规",
        "按校验SOP检查这些材料",
    ],
    "goal": [
        "第一步确认上传文件齐备，不齐备则请用户补传",
        "第二步起逐项读取文件内容做规则检查",
        "最后汇总各检查结论",
    ],
    "required_info": ["uploaded_files"],
    "response_rules": [
        "不齐备时只列出缺失文件，不进入内容检查",
        "每项检查结论明确：通过/不通过+原因",
    ],
    "slot_filling_policy": {
        "enabled": True,
        "extract_scope": "all_skill_expected_user_info",
        "multi_slot_per_turn": True,
        "skip_satisfied_steps": True,
        "description": "跨节点传递齐备性结论与各项检查结果。",
        "target_info": ["sufficiency_result", "missing_files", "check_results"],
    },
    "interruption_policy": {
        "allow_interrupt": "true",
        "description": "允许用户补充上传后继续。",
        "strategy": "resume_from_last_state",
    },
    "start_node_id": "n1_sufficiency",
    "terminal_node_ids": ["n5_request_more", "n6_report"],
    "nodes": [
        {
            "node_id": "n1_sufficiency",
            "type": "collect_info",
            "name": "文件齐备性校验",
            "instruction": (
                "只读取 task_requirement.attachments 中每个附件的 filename、kind、content_type、size，"
                "不要读取文件内容。必需文件清单=[营业执照.jpg, 合同.pdf, 发票.xlsx]。判断："
                "①全部存在→sufficiency_result='sufficient'，下一步走 n2_license；"
                "②缺失→sufficiency_result='insufficient'、missing_files=[缺失文件名清单]，下一步走 n5_request_more，"
                "并在 reply_fragment 中明确告诉用户还缺哪些文件、格式要求。"
                "【强制】无论哪种结论，结束本步骤前必须且只能调用一次 mcp__staffdeck__submit_step_result 提交结构化结果，"
                "绝不要只靠文字说明结束本步骤。提交参数示例：status='completed'，"
                "reply_fragment='给用户的话'，"
                "slot_updates={'sufficiency_result':'sufficient'|'insufficient','missing_files':[...]}，"
                "next_step_id='n2_license'（齐备）或 'n5_request_more'（不齐备，必须与上述判断一致）。"
            ),
            "optional": False,
            "expected_user_info": [],
            "allowed_actions": ["ask_user", "continue_flow", "handoff_human"],
            "knowledge_scope": {},
            "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
            "retry_policy": {},
            "metadata": {},
        },
        {
            "node_id": "n2_license",
            "type": "collect_info",
            "name": "营业执照内容核对",
            "instruction": (
                "从 task_requirement.attachments 找到文件名含 '营业执照' 的附件，用其 workspace_path"
                "（沙箱路径 attachments/<hash>-<name>）读取；图片走视觉输入，PDF 用 extract_document_text。"
                "按规则核对：<在此填写营业执照校验规则，如统一社会信用代码格式、有效期、名称一致性>。"
                "将结论写入 slot check_results.license={pass:bool, detail:str}。"
                "【强制】本步骤结束前必须且只能调用一次 mcp__staffdeck__submit_step_result 提交："
                "status='completed'，slot_updates={'check_results':{'license':{'pass':bool,'detail':str}}}，"
                "next_step_id='n3_contract'。不要只靠文字结束本步骤。"
            ),
            "optional": False,
            "expected_user_info": [],
            "allowed_actions": [
                "call_tool:read_file",
                "call_tool:extract_document_text",
                "call_tool:exec_command",
                "ask_user",
                "continue_flow",
            ],
            "knowledge_scope": {},
            "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
            "retry_policy": {"max_retries": 2, "strategy": "ask_missing"},
            "metadata": {},
        },
        {
            "node_id": "n3_contract",
            "type": "collect_info",
            "name": "合同关键条款检查",
            "instruction": (
                "从 attachments 找到 '合同' 附件，读取内容（PDF 用 extract_document_text）。"
                "按规则检查：<填写合同校验规则，如双方主体、金额、签署日期、关键条款>。"
                "结论写入 check_results.contract={pass,detail}。"
                "【强制】本步骤结束前必须且只能调用一次 mcp__staffdeck__submit_step_result 提交："
                "status='completed'，slot_updates={'check_results':{'contract':{'pass':bool,'detail':str}}}，"
                "next_step_id='n4_invoice'。不要只靠文字结束本步骤。"
            ),
            "optional": False,
            "expected_user_info": [],
            "allowed_actions": [
                "call_tool:read_file",
                "call_tool:extract_document_text",
                "call_tool:exec_command",
                "ask_user",
                "continue_flow",
            ],
            "knowledge_scope": {},
            "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
            "retry_policy": {"max_retries": 2, "strategy": "ask_missing"},
            "metadata": {},
        },
        {
            "node_id": "n4_invoice",
            "type": "collect_info",
            "name": "发票金额一致性检查",
            "instruction": (
                "从 attachments 找到 '发票' 附件（xlsx/csv 用 read_file 或 exec_command 解析）。"
                "按规则检查：<填写发票校验规则，如金额与合同一致、税号、开票日期>。"
                "结论写入 check_results.invoice={pass,detail}。"
                "【强制】本步骤结束前必须且只能调用一次 mcp__staffdeck__submit_step_result 提交："
                "status='completed'，slot_updates={'check_results':{'invoice':{'pass':bool,'detail':str}}}，"
                "next_step_id='n6_report'。不要只靠文字结束本步骤。"
            ),
            "optional": False,
            "expected_user_info": [],
            "allowed_actions": [
                "call_tool:read_file",
                "call_tool:extract_document_text",
                "call_tool:exec_command",
                "ask_user",
                "continue_flow",
            ],
            "knowledge_scope": {},
            "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
            "retry_policy": {"max_retries": 2, "strategy": "ask_missing"},
            "metadata": {},
        },
        {
            "node_id": "n5_request_more",
            "type": "response",
            "name": "请用户补传缺失文件",
            "instruction": (
                "基于 slot missing_files，明确告诉用户还缺哪些文件、格式要求，"
                "请其补充上传后重新发起校验。不要尝试内容检查。"
                "【强制】本步骤结束前必须且只能调用一次 mcp__staffdeck__submit_step_result 提交："
                "status='completed'，reply_fragment='给用户的话'，next_step_id 留空（本步骤为终点）。"
            ),
            "optional": False,
            "expected_user_info": [],
            "allowed_actions": ["answer_user", "handoff_human"],
            "knowledge_scope": {},
            "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
            "retry_policy": {},
            "metadata": {},
        },
        {
            "node_id": "n6_report",
            "type": "response",
            "name": "汇总校验结论",
            "instruction": (
                "读取 slot check_results（license/contract/invoice 等），输出一份结构化结论："
                "每项 通过/不通过+原因；总体结论；若有不通过项给出整改建议。"
                "【强制】本步骤结束前必须且只能调用一次 mcp__staffdeck__submit_step_result 提交："
                "status='completed'，reply_fragment='结构化结论正文'，next_step_id 留空（本步骤为终点）。"
            ),
            "optional": False,
            "expected_user_info": [],
            "allowed_actions": ["answer_user"],
            "knowledge_scope": {},
            "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
            "retry_policy": {},
            "metadata": {},
        },
    ],
    "edges": [
        {"source_node_id": "n1_sufficiency", "next_node_id": "n5_request_more", "condition": "insufficient", "priority": 1, "label": "文件不齐备"},
        {"source_node_id": "n1_sufficiency", "next_node_id": "n2_license", "condition": "sufficient", "priority": 2, "label": "文件齐备"},
        {"source_node_id": "n2_license", "next_node_id": "n3_contract", "condition": "default", "priority": 1, "label": "下一步"},
        {"source_node_id": "n3_contract", "next_node_id": "n4_invoice", "condition": "default", "priority": 1, "label": "下一步"},
        {"source_node_id": "n4_invoice", "next_node_id": "n6_report", "condition": "default", "priority": 1, "label": "汇总"},
    ],
}


def _request(method: str, url: str, *, headers: dict[str, str], body: Any | None) -> dict[str, Any]:
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - 受控内部地址
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code} {method} {url}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"无法连接 {url}: {exc.reason}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="创建并可选发布批量文件校验 SOP 草稿")
    parser.add_argument("--agent-id", default=os.getenv("STAFFDECK_AGENT_ID"), required=False,
                        help="目标 agent id（env: STAFFDECK_AGENT_ID）")
    parser.add_argument("--api-key", default=os.getenv("STAFFDECK_API_KEY"), required=False,
                        help="公共 API Bearer Key sd_...（env: STAFFDECK_API_KEY）")
    parser.add_argument("--base-url", default=os.getenv("STAFFDECK_BASE_URL", "http://localhost:8000/api/v1"),
                        help="公共 API 基址（env: STAFFDECK_BASE_URL，默认 http://localhost:8000/api/v1）")
    parser.add_argument("--idempotency-key", default=None, help="幂等键，默认用 skill_id")
    parser.add_argument("--publish", action="store_true", help="创建后直接发布")
    args = parser.parse_args()

    if not args.agent_id:
        raise SystemExit("--agent-id 必填（或设置 STAFFDECK_AGENT_ID）")
    if not args.api_key:
        raise SystemExit("--api-key 必填（或设置 STAFFDECK_API_KEY）")

    base = args.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Idempotency-Key": args.idempotency_key or f"sop-{SOP_CONTENT['skill_id']}",
    }

    # 1) 创建草稿
    url = f"{base}/agents/{args.agent_id}/sops"
    print(f"POST {url}")
    created = _request("POST", url, headers=headers, body={"content": SOP_CONTENT})
    print("创建成功：")
    print(json.dumps(created, ensure_ascii=False, indent=2))

    draft_id = created.get("id")
    sop_id = created.get("sop_id")
    if not (draft_id and sop_id):
        raise SystemExit("响应缺少 id / sop_id，无法继续发布。")

    # 2) 可选发布
    if args.publish:
        pub_url = f"{base}/sops/{sop_id}:publish?agent_id={args.agent_id}"
        pub_headers = {"Authorization": headers["Authorization"]}
        print(f"POST {pub_url}")
        published = _request("POST", pub_url, headers=pub_headers, body={"draft_id": draft_id})
        print("发布成功：")
        print(json.dumps(published, ensure_ascii=False, indent=2))
    else:
        print("\n草稿已落库，未发布。确认无误后可执行：")
        print(f"  curl -X POST '{base}/sops/{sop_id}:publish?agent_id={args.agent_id}' \\")
        print(f"    -H 'Authorization: Bearer {args.api_key}' \\")
        print(f"    -H 'Content-Type: application/json' \\")
        print(f"    -d '{{\"draft_id\": \"{draft_id}\"}}'")


if __name__ == "__main__":
    main()
