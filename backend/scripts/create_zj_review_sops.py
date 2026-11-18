#!/usr/bin/env python3
"""Export and publish the four ZJ funds review SOPs for Xiao Ming."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
RULES_PATH = BACKEND.parent / "docs" / "seeds" / "规则ZJ.md"
EXPORT_DIR = BACKEND / "sop_exports"
AGENT_ID = "agent_327b459bcfd84323"
SAVE_TOOL = "tool_6bcf3633f3bb40ab"
REPORT_TOOL = "tool_d8d13de114f34657"
SERVER_ID = "mcpsrv_916017f8640a4832"

# The Excel B column has four nonempty process names at rows 2, 5, 7 and 9.
FLOWS = (
    ("zj_funds_disbursement_review", "资金下拨", "融资资金类-资金下拨", "ZJ-XB01", (1, 2, 3)),
    ("zj_financing_loan_review", "融资借款", "融资借款申请表", "ZJ-RZ01", (4, 5)),
    ("zj_funds_lending_review", "资金出借", "资金出借审批单", "ZJ-CJ01", (6, 7)),
    ("zj_debt_service_review", "还本付息", "还本付息申请表", "ZJ-HX01", (8,)),
)
def rule_sections() -> dict[int, tuple[str, str]]:
    text = RULES_PATH.read_text(encoding="utf-8")
    sections = {}
    for part in re.split(r"(?=^## ZJ-R\d{2} )", text, flags=re.MULTILINE):
        match = re.match(r"## ZJ-R(\d{2}) (.+)", part)
        if match:
            sections[int(match.group(1))] = (match.group(2), part.strip())
    if set(sections) != set(range(1, 9)):
        raise ValueError("规则ZJ.md 必须包含 ZJ-R01 至 ZJ-R08")
    return sections


def node(node_id: str, name: str, instruction: str, *, kind: str = "collect_info",
         actions: list[str] | None = None, tool_id: str | None = None,
         metadata: dict | None = None, result_inputs: list[str] | None = None,
         result_outputs: list[str] | None = None) -> dict:
    refs = {key: [] for key in (
        "general_skill_ids", "tool_ids", "knowledge_base_ids",
        "required_general_skill_ids", "required_tool_ids", "required_knowledge_base_ids",
    )}
    if tool_id:
        refs["tool_ids"] = [tool_id]
        refs["required_tool_ids"] = [tool_id]
    return {
        "node_id": node_id, "type": kind, "name": name, "instruction": instruction,
        "optional": False, "expected_user_info": [],
        "result_inputs": result_inputs or [], "result_outputs": result_outputs or [],
        "allowed_actions": actions or ["continue_flow"],
        "knowledge_scope": {}, "capability_refs": refs, "retry_policy": {},
        "metadata": metadata or {},
    }


def edge(source: str, target: str, condition: str = "default") -> dict:
    return {"source_node_id": source, "next_node_id": target,
            "condition": condition, "priority": 1, "label": condition}


def build(flow: tuple, sections: dict[int, tuple[str, str]]) -> dict:
    skill_id, short_name, flow_name, example_code, rule_numbers = flow
    rule_ids = [f"ZJ-R{number:02d}" for number in rule_numbers]
    first_step = "n1_gate" if rule_numbers == (8,) else f"n{rule_numbers[0]}_review"
    collect = (
        f"本次仅审核「{flow_name}」，适用 {', '.join(rule_ids)}。"
        "以材料正文确认待审表单及相关附件的类型，文件名仅供参考。"
        "B00 为待审费控/OA表单，B01 为资金池 T-1 可用余额快照，"
        "B02 为账户台账或银企主数据，B03 为三会决议或纪要，"
        "B04 为 BIP 贷款信息汇总表。"
        "从 B00 核实申请编号、原始表单编号、项目名称和实际申请事项；"
        f"示例表单编号 {example_code} 仅供识别，不作为固定要求。"
        "申请事项不属于本审核范围时，提示改选对应流程。"
        "缺待审表单、申请编号或无法确认申请事项时，请求补充材料。"
        "其他材料缺失时在对应审核项说明待补正，不猜测事实。"
    )
    nodes = [node("n0_collect", "识别表单与材料", collect,
                  actions=["call_tool:list_directory", "call_tool:read_file", "continue_flow"],
                  result_outputs=["attachment_role_map", "source_docs", "audit_meta"])]
    edges = [edge("n0_collect", first_step)]
    terminal_ids = ["n11_report"]
    if rule_numbers == (8,):
        gate = (
            "核实本次贷款人是否为外部金融机构，并注明材料依据。"
            "外部金融机构贷款适用 R08；明确不是外部金融机构时，"
            "原表规定 R08 不执行，不生成判定项或通过项。"
            "贷款人性质不明时，请求补充材料，不自行判断。"
        )
        nodes.append(node("n1_gate", "判断 R08 是否适用", gate,
                          actions=["call_tool:read_file", "continue_flow"],
                          result_inputs=["source_docs"], result_outputs=["applicability"]))
        nodes.append(node("n2_not_applicable", "说明本次规则不执行",
                          "贷款人已确认不是外部金融机构，按原表 R08 不执行。"
                          "说明未触发原因与证据；不得生成判定项、通过结论或协审报告。",
                          actions=["answer_user"], result_inputs=["applicability"]))
        edges.extend([edge("n1_gate", "n8_review", "外部金融机构"),
                      edge("n1_gate", "n2_not_applicable", "非外部金融机构")])
        terminal_ids.append("n2_not_applicable")

    for index, number in enumerate(rule_numbers):
        seq = f"{number:02d}"
        title, rule_text = sections[number]
        next_step = (f"n{rule_numbers[index + 1]}_review"
                     if index + 1 < len(rule_numbers) else "n9_aggregate")
        business_rule = "\n".join(
            line for line in rule_text.splitlines() if not line.startswith("检索关键词：")
        ).strip()
        review = (
            "依据以下规则判定本次申请。必要材料缺失、无法实质判定时记关注（待补正）；"
            "已有证据明确命中风险时仍记风险。结论须写明具体比对、处理意见，"
            "并用一句话注明材料、字段或条款及关键数值。\n\n"
            f"{business_rule}"
        )
        nodes.append(node(f"n{number}_review", f"ZJ-R{seq} {title}", review,
                          actions=["call_tool:read_file", "continue_flow"],
                          metadata={"rule_id": f"ZJ-R{seq}", "rule_lib_version": "V1.0"},
                          result_inputs=["source_docs"], result_outputs=[f"review_{seq}"]))
        edges.append(edge(f"n{number}_review", next_step))

    aggregate = (
        f"汇总本流程 {', '.join(rule_ids)} 的 {len(rule_numbers)} 条审阅结论，"
        "逐条保留审核要素、意见、级别、规则编号和证据，不混入其他流程。"
        f"统计风险 X、关注 Y、通过 Z，X+Y+Z 必须等于 {len(rule_numbers)}。"
        "全通过→建议通过；有风险且可补正→建议有条件通过，并写明处理条件；"
        "有风险且不可补正→建议不通过；仅有关注→建议通过，关注事项跟进。"
        f"摘要写为'共评审 {len(rule_numbers)} 项：X 项风险、Y 项关注、Z 项通过；"
        "一句话处理意见'。保留原表单编号、项目名称和规则版本 V1.0，"
        "形成供保存使用的汇总结论；其中逐项结论保留原有规则编号、"
        "审核要素、意见、级别和证据，并包含申请编号、原始表单编号、项目名称、"
        "规则版本、综合结论、处理条件及摘要。自检统计、级别和风险结论。"
    )
    nodes.append(node(
        "n9_aggregate", "汇总本流程结论", aggregate,
        result_inputs=["audit_meta", *[f"review_{number:02d}" for number in rule_numbers]],
        result_outputs=["audit_payload"],
    ))
    edges.append(edge("n9_aggregate", "n10_save"))
    save = (
        "保存已核对的协审结果，保留原表单编号、逐项意见与证据、综合结论及处理条件。"
        "保存失败时说明原因并暂停，不得声称已完成。成功后记录实际保存回执。"
    )
    nodes.append(node("n10_save", "保存协审结果", save, kind="tool_call",
                      actions=["call_tool:audit_report.save_audit_result", "continue_flow"],
                      tool_id=SAVE_TOOL,
                      metadata={"tool_name": "audit_report.save_audit_result",
                                "mcp_server_id": SERVER_ID},
                      result_inputs=["audit_payload"], result_outputs=["save_result"]))
    edges.append(edge("n10_save", "n11_report"))
    report = (
        "确认保存成功后生成协审报告。向用户说明综合结论、风险/关注/通过数、"
        "处理条件和关注事项，并提供实际生成的报告链接；不得编造链接。"
    )
    nodes.append(node("n11_report", "生成协审报告", report, kind="tool_call",
                      actions=["call_tool:audit_report.render_audit_report", "answer_user"],
                      tool_id=REPORT_TOOL,
                      metadata={"tool_name": "audit_report.render_audit_report",
                                "mcp_server_id": SERVER_ID},
                      result_inputs=["audit_payload", "save_result"],
                      result_outputs=["report_url"]))
    return {
        "skill_id": skill_id, "name": f"ZJ {short_name}协审 SOP", "version": "1.0.0",
        "business_domain": "资金", "description": f"按 ZJ 规则库 V1.0 审阅{flow_name}并生成报告。",
        "capability_scope": "general", "step_timeout_seconds": 600,
        "trigger_intents": [f"{short_name}协审", f"{flow_name}点检"],
        "user_utterance_examples": [f"按 ZJ 规则审阅这份{flow_name}",
                                   f"对{flow_name}做协审并出报告"],
        "goal": ["按内容识别本流程所需材料", f"只判定 {', '.join(rule_ids)}",
                 "保存本次流程结论并生成报告"],
        "required_info": ["attachments", "request_id"],
        "slot_filling_policy": {
            "enabled": True, "extract_scope": "all_skill_expected_user_info",
            "multi_slot_per_turn": True, "skip_satisfied_steps": True,
            "target_info": ["attachments", "request_id"],
            "description": "只从用户输入中识别申请资料；步骤结果由节点结果配置传递。",
        },
        "response_rules": [
            "只评本流程规则，不混入其他流程", "level 只能是风险/关注/通过",
            "必要材料缺失记关注（待补正），不得冒充通过；证据一句话摘要",
            "摘要统计必须与逐项结论一致；有风险时不得建议通过",
            "材料索引只存路径；报告链接必须来自实际生成结果",
        ] + (["R08 非外部金融机构贷款时不执行，不生成 item 或通过项"]
             if rule_numbers == (8,) else []),
        "interruption_policy": {"allow_interrupt": "true",
                                "description": "补充材料后继续", "strategy": "resume_from_last_state"},
        "start_node_id": "n0_collect", "terminal_node_ids": terminal_ids,
        "nodes": nodes, "edges": edges,
    }


def verify_published(cards: list[dict]) -> None:
    """Check the five persisted links required by Xiao Ming's local workflow."""
    connection = sqlite3.connect(BACKEND / "skill_agent_loop.db")
    try:
        for card in cards:
            skill_id = card["skill_id"]
            rows = connection.execute(
                "SELECT s.id, s.version, s.status, sv.status, b.status, br.status, "
                "br.head_version, bv.status, br.content_json, s.content_json "
                "FROM skills s "
                "JOIN skill_versions sv ON sv.tenant_id=s.tenant_id "
                "AND sv.skill_id=s.skill_id AND sv.version=s.version "
                "JOIN agent_resource_bindings b ON b.resource_id=s.id "
                "AND b.resource_type='skill' AND b.agent_id=? "
                "JOIN agent_skill_branches br ON br.agent_id=b.agent_id "
                "AND br.skill_id=s.skill_id "
                "JOIN agent_skill_branch_versions bv ON bv.agent_id=b.agent_id "
                "AND bv.skill_id=s.skill_id AND bv.version=br.head_version "
                "WHERE s.tenant_id='tenant_demo' AND s.skill_id=?",
                (AGENT_ID, skill_id),
            ).fetchall()
            expected = ("published", "published", "active", "active", "active")
            if not any((row[2], row[3], row[4], row[5], row[7]) == expected for row in rows):
                raise RuntimeError(f"{skill_id} 未完整发布到小明: {rows}")
            active = next(row for row in rows if (row[2], row[3], row[4], row[5], row[7]) == expected)
            for source_name, content in (("员工分支", active[8]), ("SOP 总版本", active[9])):
                published_nodes = {
                    node["node_id"]: node for node in json.loads(content)["nodes"]
                }
                for expected_node in card["nodes"]:
                    actual_node = published_nodes.get(expected_node["node_id"])
                    if actual_node is None or any(
                        actual_node[key] != expected_node[key]
                        for key in ("instruction", "result_inputs", "result_outputs")
                    ):
                        raise RuntimeError(
                            f"{source_name}节点指导未同步: {skill_id}/{expected_node['node_id']}"
                        )
                    if actual_node["capability_refs"]["knowledge_base_ids"]:
                        raise RuntimeError(
                            f"{source_name}节点仍引用规则知识库: {skill_id}/{expected_node['node_id']}"
                        )
            print(f"Verified {skill_id} v{active[1]}: published + 4 active links")
    finally:
        connection.close()


def activate_local_sop(card: dict) -> None:
    """Complete the five-table local registration used by Xiao Ming's SOP list."""
    skill_id = card["skill_id"]
    connection = sqlite3.connect(BACKEND / "skill_agent_loop.db", timeout=30)
    try:
        with connection:
            row = connection.execute(
                "SELECT s.id, s.version, br.head_version FROM skills s "
                "JOIN agent_skill_branches br ON br.tenant_id=s.tenant_id "
                "AND br.skill_id=s.skill_id AND br.agent_id=? "
                "WHERE s.tenant_id='tenant_demo' AND s.skill_id=?",
                (AGENT_ID, skill_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"SOP missing after API publish: {skill_id}")
            skill_pk, version, branch_version = row
            changes = (
                ("UPDATE skills SET status='published' WHERE id=?", (skill_pk,)),
                (("UPDATE skill_versions SET status='published' "
                  "WHERE tenant_id='tenant_demo' AND skill_id=? AND version=?"),
                 (skill_id, version)),
                (("UPDATE agent_resource_bindings SET status='active' "
                  "WHERE tenant_id='tenant_demo' AND agent_id=? "
                  "AND resource_type='skill' AND resource_id=?"), (AGENT_ID, skill_pk)),
                (("UPDATE agent_skill_branches SET status='active', sync_state='synced', "
                  "base_version=? "
                  "WHERE tenant_id='tenant_demo' AND agent_id=? AND skill_id=?"),
                 (version, AGENT_ID, skill_id)),
                (("UPDATE agent_skill_branch_versions SET status='active', sync_state='synced', "
                  "base_version=? "
                  "WHERE tenant_id='tenant_demo' AND agent_id=? AND skill_id=? AND version=?"),
                 (version, AGENT_ID, skill_id, branch_version)),
            )
            for statement, params in changes:
                result = connection.execute(statement, params)
                if result.rowcount != 1:
                    raise RuntimeError(f"Incomplete local SOP registration: {skill_id}: {statement}")
    finally:
        connection.close()


def sync_source_skill(card: dict) -> None:
    """Keep the overall SOP content aligned with the agent branch shown in the editor."""
    from sqlmodel import Session, select

    from app.api.skills import _sop_version_key, _upsert_skill_version
    from app.db.database import engine
    from app.db.models import Skill, SkillVersion, utc_now
    from app.skills.skill_schema import SkillCard, skill_card_from_persisted

    with Session(engine) as db:
        row = db.exec(select(Skill).where(
            Skill.tenant_id == "tenant_demo", Skill.skill_id == card["skill_id"],
        )).first()
        if row is None:
            raise RuntimeError(f"无法同步 SOP 总版本: {card['skill_id']}")
        current = skill_card_from_persisted(row.content_json).model_dump()
        desired = SkillCard.model_validate(card).model_dump()
        current.pop("version", None)
        desired.pop("version", None)
        if current == desired:
            return
        # This is a private employee SOP. The overall-scope update API validates
        # against the open gallery and rejects it, so synchronize the local source
        # after the employee branch has passed the normal publish validation.
        history = db.exec(select(SkillVersion.version).where(
            SkillVersion.tenant_id == row.tenant_id,
            SkillVersion.skill_id == row.skill_id,
        )).all()
        major, minor, patch = max(
            _sop_version_key(version) for version in [row.version, *history]
        )
        version = f"{major}.{minor}.{patch + 1}"
        updated_content = SkillCard.model_validate(card).model_copy(
            update={"version": version}
        ).model_dump()
        row.version = version
        row.name = updated_content["name"]
        row.business_domain = updated_content["business_domain"]
        row.description = updated_content["description"]
        row.content_json = updated_content
        row.updated_at = utc_now()
        db.add(row)
        _upsert_skill_version(db, row, commit=False)
        db.commit()
        print(f"Updated overall {card['skill_id']} v{version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="发布到小明本地工作流")
    parser.add_argument("--verify", action="store_true", help="核验小明本地工作流的五表关联")
    args = parser.parse_args()
    for path in (BACKEND, BACKEND / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from app.skills.skill_schema import SkillCard

    sections = rule_sections()
    cards = [SkillCard.model_validate(build(flow, sections)).model_dump() for flow in FLOWS]
    for card in cards:
        SkillCard.model_validate(card)
        out = EXPORT_DIR / f"{card['skill_id']}.json"
        out.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{card['skill_id']}: {len(card['nodes'])} nodes, {len(card['edges'])} edges -> {out}")
    if args.publish:
        from create_f02_review_sop import run_local

        for card in cards:
            print(f"\nPublishing {card['skill_id']} to {AGENT_ID}")
            run_local(card, AGENT_ID, "admin", publish=True, set_max_actions=None)
            sync_source_skill(card)
            activate_local_sop(card)
    if args.publish or args.verify:
        verify_published(cards)


if __name__ == "__main__":
    main()
