#!/usr/bin/env python3
"""本地直接把「批量文件合规校验 SOP」落库到 SQLite（绕过公共 API Key）。

适用：本机已有运行/停用的 StaffDeck 后端，且数据库为默认 SQLite。
以 admin 用户身份，把 SOP 绑定到指定 agent（默认 agent_327b459bcfd84323）。

做法：直接调用内部路由函数 create_skill / publish_skill，并显式传入
      db 会话与 current_user（其 Depends 默认值在直接调用时被忽略），
      从而复用全部校验、SOP 嵌套检查与 agent 绑定逻辑。

用法（在 backend 目录下执行）：
    python scripts/create_validation_sop_local.py
    python scripts/create_validation_sop_local.py --publish
    python scripts/create_validation_sop_local.py --agent-id <id> --username admin
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# 让脚本能 import app 包（从 backend 目录运行即可）。
BACKEND = Path(__file__).resolve().parents[1]
for _p in (BACKEND, BACKEND / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sqlmodel import Session, select  # noqa: E402

from app.db.database import engine  # noqa: E402
from app.db.models import User  # noqa: E402
from app.api.skills import create_skill, publish_skill  # noqa: E402
from app.skills.skill_schema import SkillCard, SkillCreateRequest  # noqa: E402


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


def main() -> None:
    parser = argparse.ArgumentParser(description="本地落库批量文件校验 SOP（admin 身份）")
    parser.add_argument("--agent-id", default="agent_327b459bcfd84323",
                        help="目标 agent id（默认 agent_327b459bcfd84323）")
    parser.add_argument("--username", default="admin", help="登录用户名（默认 admin）")
    parser.add_argument("--publish", action="store_true", help="创建后直接发布")
    parser.add_argument("--status", default="draft", choices=["draft", "published", "archived"])
    args = parser.parse_args()

    session = Session(engine)

    admin = session.exec(
        select(User).where(User.username == args.username)
    ).first()
    if not admin:
        raise SystemExit(f"未找到用户 '{args.username}'，请检查用户名。")
    tenant_id = admin.tenant_id
    print(f"以用户 {admin.username}（tenant={tenant_id}）向 agent {args.agent_id} 落库 SOP。")

    try:
        request = SkillCreateRequest(
            tenant_id=tenant_id,
            content=SkillCard.model_validate(SOP_CONTENT),
            status=args.status,
        )
        created = create_skill(request, agent_id=args.agent_id, db=session, current_user=admin)
    except Exception as exc:  # noqa: BLE001 - 直接展示后端校验错误
        session.rollback()
        raise SystemExit(f"创建失败：{type(exc).__name__}: {exc}") from exc

    print("创建成功：")
    print(f"  skill_id = {created.skill_id}")
    print(f"  id       = {created.id}")
    print(f"  status   = {created.status}")
    print(f"  name     = {created.name}")

    if args.publish or args.status == "published":
        try:
            published = publish_skill(
                created.skill_id, tenant_id=tenant_id, agent_id=args.agent_id,
                db=session, current_user=admin,
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise SystemExit(f"发布失败：{type(exc).__name__}: {exc}") from exc
        print(f"已发布：status = {published.status}")

    print("\n完成。可在对应 agent 的 SOP 列表中看到「批量文件合规校验 SOP」。")


if __name__ == "__main__":
    main()
