#!/usr/bin/env python3
"""把 agent「小明」(agent_327b459bcfd84323) 名下的 SOP 合并成一条贯穿流程。

设计选择（与用户确认）：
- 合并方式：扁平合并串联 —— 去重 + 全局重编号，按「采购合规生命周期」顺序重新串联成一张图。
- 输出落点：写回数据库，在 agent_skill_branches 下为小明新建一个合并后的 SOP 分支。

去重规则：
- 排除 skill_new_blank_001（用户明确要求）。
- 排除 sop_new_blank_001（「新建空白SOP」是创建空白 SOP 的元工具向导，不属于采购合规检查，
  并入合规流程语义不通，故一并排除；如需纳入可取消下方 EXCLUDE 中的该项）。
- procurement-doc-compliance-check-sop 已是覆盖「附件→采购方式→资格条件→评分→文本→保证金→
  商务合同→异议澄清弃标」的主干，其余若干独立子 SOP 是上述主干中某个分项的独立版，
  合并时统一并入主干对应分项节点（去重），避免同一检查出现多次。

实现：直接读 SQLite（不经由公共 API）。每个合并节点优先借用源 SOP 原始节点定义
（node_id 改为全局唯一 mNN），未对应源节点的少量聚合/收口节点（m32 汇总、m35 最终结论）
内联编写。边按生命周期顺序串联，否决类路由到统一否决终端 m_reject，风险/异常类路由到
统一转人工终端 m_handoff。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(__file__), "..", "skill_agent_loop.db")
AGENT_ID = "agent_327b459bcfd84323"

# 排除名单（skill_id）
EXCLUDE = {"skill_new_blank_001", "sop_new_blank_001"}

# 合并节点规格：(merged_id, source_skill_id, source_node_id)
#   source_skill_id 为 None 表示内联新节点。
MERGED_NODES = [
    # ---- 上游：合作方入库尽调 ----
    ("m01", "partner_onboarding_dd", "collect_partner_info"),
    ("m02", "partner_onboarding_dd", "query_contract_history"),
    ("m03", "partner_onboarding_dd", "evaluate_compliance"),
    ("m04", "partner_onboarding_dd", "issue_onboarding_opinion"),
    # ---- 采购资料收集 + 附件点验（合并 sop / skill_procurement_attachment_check_001 / 主干 node_001~003）----
    ("m05", "procurement-doc-compliance-check-sop", "node_001"),
    ("m06", "procurement-doc-compliance-check-sop", "node_003"),
    ("m07", "skill_procurement_attachment_check_001", "node_reject_response"),
    ("m08", "skill_procurement_attachment_check_001", "node_minutes_missing_prompt_response"),
    # ---- 采购方式合规（合并 主干 node_004/005 + skill_procurement_compliance_check）----
    ("m09", "skill_procurement_compliance_check", "node_check_method_consistency"),
    # ---- 资格条件合规（合并 主干 node_008/009 + skill_procurement_qualification_review）----
    ("m10", "skill_procurement_qualification_review", "node_judge_qualification_violation"),
    # ---- 评分细则合规（合并 主干 node_013/014 + skill_procurement_compliance_check_001）----
    ("m11", "skill_procurement_compliance_check_001", "node_004"),
    # ---- 招标文件文本规范性（合并 主干 node_006/007 + 两个文本检查 SOP）----
    ("m12", "procurement-doc-compliance-check-sop", "node_006"),
    ("m13", "skill_bidding_document_text_check", "node_3"),
    # ---- 投标保证金/保函（合并 主干 node_010 + skill_bid_deposit_check_001 + procurement_bid_deposit_check_skill）----
    ("m14", "skill_bid_deposit_check_001", "node_005"),
    # ---- 商务及合同核心条款（合并 主干 node_011/012 + skill_contract_high_risk_clause_check）----
    ("m15", "procurement-doc-compliance-check-sop", "node_011"),
    ("m16", "skill_contract_high_risk_clause_check", "node_judge_high_risk_clause"),
    # ---- 异议澄清弃标（合并 主干 node_015 + skill_bid_objection_clarification_abandon_check）----
    ("m17", "skill_bid_objection_clarification_abandon_check", "node_judge_risk_points"),
    # ---- 下游：合同条款风险审查 ----
    ("m18", "contract_risk_review", "collect_contract_info"),
    ("m19", "contract_risk_review", "query_review_standards"),
    ("m20", "contract_risk_review", "retrieve_historical_cases"),
    ("m21", "contract_risk_review", "evaluate_risk_level"),
    ("m22", "contract_risk_review", "output_review_result"),
    # ---- 条款修改建议 ----
    ("m23", "skill_clause_modification", "collect_clause_info"),
    ("m24", "skill_clause_modification", "query_contract_reference"),
    ("m25", "skill_clause_modification", "deliver_suggestion"),
    # ---- 通用：批量文件合规校验（签约材料齐备性）----
    ("m26", "batch_file_validation", "n1_sufficiency"),
    ("m27", "batch_file_validation", "n2_license"),
    ("m28", "batch_file_validation", "n3_contract"),
    ("m29", "batch_file_validation", "n4_invoice"),
    ("m30", "batch_file_validation", "n5_request_more"),
    ("m31", "batch_file_validation", "n6_report"),
    # ---- 收口：统一否决终端 / 统一转人工终端 / 协审结论汇总 / 最终结论 ----
    ("m_reject", "procurement-doc-compliance-check-sop", "node_016"),
    ("m_handoff", "procurement-doc-compliance-check-sop", "node_018"),
    ("m32", None, None),  # 内联：协审要点汇总（合并 AI 卡片收集/聚合节点）
    ("m33", "skill_ai_review_opinion_card_v1", "node_confirm_review_content"),
    ("m34", "skill_ai_review_opinion_card_v1", "node_generate_html_report"),
    ("m35", None, None),  # 内联：最终合规结论
]

# 边：(source, target, condition, label)
EDGES = [
    # 上游尽调
    ("m01", "m02", "slots_filled(company_name, unified_social_credit_code)", "信息完整"),
    ("m02", "m03", "tool_success", "查询成功"),
    ("m02", "m_handoff", "tool_fail", "查询失败转人工"),
    ("m03", "m04", "compliance_result == pass", "尽调通过"),
    ("m03", "m_handoff", "compliance_result == risk", "存在风险升级法务"),
    ("m04", "m05", "default", "进入采购资料审查"),
    # 资料收集 + 附件点验
    ("m05", "m06", "default", "进入附件点验"),
    ("m06", "m07", "否决", "采购方案或招标文件缺失"),
    ("m06", "m08", "关注", "仅决策纪要缺失"),
    ("m06", "m09", "通过", "附件齐全"),
    ("m08", "m09", "default", "提示后继续"),
    # 采购方式
    ("m09", "m_reject", "否决", "采购方式不一致或超预算"),
    ("m09", "m10", "通过", "方式合规"),
    # 资格条件
    ("m10", "m_reject", "否决", "资格条件违规"),
    ("m10", "m11", "通过", "资格合规"),
    # 评分细则
    ("m11", "m_reject", "否决", "评分细则违规"),
    ("m11", "m12", "通过", "评分合规"),
    # 招标文件读取 + 文本
    ("m12", "m13", "default", "文本读取成功"),
    ("m12", "m_handoff", "tool_fail", "读取失败转人工"),
    ("m13", "m14", "default", "文本校验完成"),
    # 保证金
    ("m14", "m15", "default", "保证金校验完成"),
    # 商务合同核心条款
    ("m15", "m16", "default", "条款提取成功"),
    ("m15", "m_handoff", "tool_fail", "提取失败转人工"),
    ("m16", "m17", "default", "合同核心条款校验完成"),
    # 异议澄清弃标
    ("m17", "m18", "default", "异议澄清弃标校验完成"),
    # 下游合同风险审查
    ("m18", "m19", "contract_type_filled", "合同类型已识别"),
    ("m19", "m20", "standards_loaded", "标准已检索"),
    ("m20", "m21", "tool_call_completed", "判例已检索"),
    ("m21", "m22", "risk_level_low_or_medium", "低风险可自动输出"),
    ("m21", "m_handoff", "risk_level_high_or_no_precedent", "高风险升级法务"),
    ("m22", "m23", "default", "进入条款修改建议"),
    ("m23", "m24", "clause_content与modification_request均已收集", "信息完整"),
    ("m23", "m25", "信息完整但用户明确要求跳过检索", "跳过检索"),
    ("m24", "m25", "工具调用成功或触发降级策略", "参考已获取"),
    # 批量文件 / 签约材料齐备性
    ("m25", "m26", "default", "进入签约材料齐备性校验"),
    ("m26", "m27", "sufficiency_result == sufficient", "材料齐备"),
    ("m26", "m30", "sufficiency_result == insufficient", "材料缺失"),
    ("m27", "m28", "default", "营业执照核对完成"),
    ("m28", "m29", "default", "合同核对完成"),
    ("m29", "m31", "default", "发票核对完成"),
    # 协审结论汇总 + 最终结论
    ("m31", "m32", "default", "进入协审结论汇总"),
    ("m32", "m33", "default", "要点已聚合"),
    ("m33", "m34", "default", "确认完成"),
    ("m34", "m35", "default", "报告已生成"),
]

INLINE_NODES = {
    "m32": {
        "node_id": "m32",
        "type": "collect_info",
        "name": "汇总协审要点与综合结论",
        "instruction": (
            "汇总前置各阶段（合作方尽调、采购文件合规各分项、合同审查、签约材料校验）产生"
            "的全部评审要点与结论，关联 rule_id 与证据附件信息，统计通过/关注/否决/转人工数量，"
            "推导综合审批结论（结论、条件、摘要）。直接复用已收集的 slots 信息写入对应 slot，"
            "无需向用户追问。若关键材料缺失或字段无法核验，标记需转人工。"
        ),
        "optional": False,
        "expected_user_info": [],
        "allowed_actions": ["continue_flow", "handoff_human"],
        "knowledge_scope": {},
        "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
        "retry_policy": {},
        "metadata": {},
        "sub_sop_id": None,
    },
    "m35": {
        "node_id": "m35",
        "type": "response",
        "name": "反馈最终合规结论",
        "instruction": (
            "汇总全链路合规校验结论（合作方尽调、采购文件各分项审查、合同审查、签约材料校验），"
            "输出结构化最终报告：总体结论、各分项通过/关注/否决状态与整改建议、可下载的协审/合规报告链接。"
            "无法闭环时转人工，不要只说请稍候。"
        ),
        "optional": False,
        "expected_user_info": [],
        "allowed_actions": ["answer_user", "handoff_human"],
        "knowledge_scope": {},
        "capability_refs": {"tool_ids": [], "general_skill_ids": [], "knowledge_base_ids": []},
        "retry_policy": {},
        "metadata": {},
        "sub_sop_id": None,
    },
}


def load_source_nodes(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    cur = conn.cursor()
    cur.execute(
        "SELECT skill_id, content_json FROM agent_skill_branches WHERE agent_id = ?",
        (AGENT_ID,),
    )
    src: dict[tuple[str, str], dict] = {}
    for skill_id, content_json in cur.fetchall():
        if not content_json:
            continue
        content = json.loads(content_json)
        for n in content.get("nodes", []):
            src[(skill_id, n.get("node_id"))] = n
    return src


def build_nodes(src: dict[tuple[str, str], dict]) -> list[dict]:
    nodes: list[dict] = []
    seen = set()
    for merged_id, skill_id, src_node_id in MERGED_NODES:
        if merged_id in seen:
            raise SystemExit(f"重复合并节点 id: {merged_id}")
        seen.add(merged_id)
        if skill_id is None:
            node = dict(INLINE_NODES[merged_id])
        else:
            key = (skill_id, src_node_id)
            base = src.get(key)
            if base is None:
                raise SystemExit(f"源节点缺失: {key}")
            node = dict(base)
            node["node_id"] = merged_id
            node["sub_sop_id"] = None
            # 保证字段完整，且类型与 SkillGraphNode schema 一致
            # （capability_refs/retry_policy 必须是 dict，不能是 list/tuple）
            defaults = {
                "optional": False,
                "expected_user_info": [],
                "allowed_actions": [],
                "knowledge_scope": {},
                "capability_refs": {},
                "retry_policy": {},
                "metadata": {},
            }
            for fld, default in defaults.items():
                if fld not in node or node[fld] is None:
                    node[fld] = default
            # 兼容源节点里历史遗留的非法类型（如 [{}]）
            if not isinstance(node.get("capability_refs"), dict):
                node["capability_refs"] = {}
            if not isinstance(node.get("retry_policy"), dict):
                node["retry_policy"] = {}
        nodes.append(node)
    return nodes


def build_content(nodes: list[dict]) -> dict:
    return {
        "skill_id": "merged_procurement_compliance_flow",
        "name": "招采合规一体化全流程（合并）",
        "version": "1.0.0",
        "business_domain": "采购与供应链合规",
        "description": (
            "将小明名下的采购合规相关 SOP 合并为一条贯穿流程：合作方入库尽调 → 采购资料收集与附件点验"
            " → 采购方式/资格条件/评分细则合规 → 招标文件文本/保证金/商务合同核心条款/异议澄清弃标校验"
            " → 合同条款风险审查与条款修改建议 → 签约材料齐备性校验 → 协审结论汇总与最终报告。"
            "否决类统一终止于否决结论节点，风险/异常类统一转人工。"
        ),
        "capability_scope": "general",
        "step_timeout_seconds": 1800,
        "trigger_intents": [
            "采购文件合规审查", "招采合规全流程", "合并采购合规校验",
            "供应商入库尽调", "合同合规审查",
        ],
        "user_utterance_examples": [
            "帮我做一份采购文件的全面合规审查",
            "按合并流程跑一遍招采合规检查",
        ],
        "goal": [
            "按采购合规生命周期顺序完成全链路校验",
            "任一分项否决即终止并给出否决结论与整改建议",
            "风险/异常统一升级人工，其余汇总为最终合规报告",
        ],
        "required_info": ["project_name", "attachments", "budget_amount"],
        "slot_filling_policy": {
            "enabled": True,
            "extract_scope": "all_skill_expected_user_info",
            "multi_slot_per_turn": True,
            "skip_satisfied_steps": True,
            "description": "跨节点传递各分项校验结论与综合审批结果。",
            "target_info": ["check_results", "review_items", "final_conclusion"],
        },
        "response_rules": [
            "否决类结论明确列出否决原因与整改建议",
            "关注类风险项汇总提示但不阻断流程",
            "最终结论结构化输出总体状态与报告链接",
        ],
        "interruption_policy": {
            "allow_interrupt": "true",
            "description": "允许用户在任意节点补充材料后继续。",
            "strategy": "resume_from_last_state",
        },
        "start_node_id": "m01",
        "terminal_node_ids": ["m_reject", "m_handoff", "m35", "m30", "m07"],
        "nodes": nodes,
        "edges": [
            {"source_node_id": s, "next_node_id": t, "condition": c, "priority": 1, "label": l}
            for (s, t, c, l) in EDGES
        ],
    }


def get_template_meta(conn: sqlite3.Connection) -> tuple[str, str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT tenant_id, metadata_json FROM agent_skill_branches "
        "WHERE agent_id = ? LIMIT 1",
        (AGENT_ID,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit("找不到小明的任何分支，无法获取 tenant/模板元数据")
    tenant_id, meta_json = row
    meta = json.loads(meta_json) if meta_json else {}
    meta = dict(meta)
    meta["created_from_agent"] = True
    meta["owner_agent_id"] = AGENT_ID
    meta["scope"] = "agent_private"
    meta["visibility"] = "agent_private"
    meta["merged_from"] = "xiaoming_sops_except_blank"
    return tenant_id, json.dumps(meta, ensure_ascii=False)


def main() -> None:
    db_path = os.path.abspath(DB)
    if not os.path.exists(db_path):
        raise SystemExit(f"数据库不存在: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    src = load_source_nodes(conn)
    # 校验排除项确实不在合并清单里
    used_skill_ids = {s for (_, s, _) in MERGED_NODES if s}
    excluded_present = used_skill_ids & EXCLUDE
    if excluded_present:
        raise SystemExit(f"合并清单误包含被排除的 skill_id: {excluded_present}")

    nodes = build_nodes(src)
    content = build_content(nodes)
    tenant_id, meta_json = get_template_meta(conn)

    branch_id = f"agentbranch_{AGENT_ID}_merged_procurement_compliance_flow"
    skill_id = "merged_procurement_compliance_flow"

    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM agent_skill_branches WHERE id = ?", (branch_id,)
    )
    exists = cur.fetchone()

    content_json = json.dumps(content, ensure_ascii=False)

    if exists:
        cur.execute(
            "UPDATE agent_skill_branches SET content_json = ?, head_version = ?, "
            "status = 'active', sync_state = 'diverged', metadata_json = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (content_json, content["version"], meta_json, branch_id),
        )
        print(f"已更新已存在的合并分支: {branch_id}")
    else:
        cur.execute(
            "INSERT INTO agent_skill_branches "
            "(id, tenant_id, agent_id, skill_id, source_skill_id, base_version, "
            "head_version, content_json, status, sync_state, metadata_json, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'diverged', ?, "
            "datetime('now'), datetime('now'))",
            (branch_id, tenant_id, AGENT_ID, skill_id, skill_id,
             content["version"], content["version"], content_json, meta_json),
        )
        print(f"已新建合并分支: {branch_id}")

    conn.commit()
    conn.close()

    print(f"\n合并结果：节点 {len(content['nodes'])} 个，边 {len(content['edges'])} 条")
    print("排除的 skill_id：", sorted(EXCLUDE))
    print("起点：", content["start_node_id"], " 终点：", content["terminal_node_ids"])


if __name__ == "__main__":
    main()
