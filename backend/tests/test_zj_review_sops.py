"""Focused checks for the four exported ZJ funds review workflows."""
from __future__ import annotations

import json
from pathlib import Path

from app.skills.skill_schema import SkillCard

EXPORT_DIR = Path(__file__).resolve().parents[1] / "sop_exports"
RULES_PATH = Path(__file__).resolve().parents[2] / "docs" / "seeds" / "规则ZJ.md"
EXPECTED = {
    "zj_funds_disbursement_review": {"ZJ-R01", "ZJ-R02", "ZJ-R03"},
    "zj_financing_loan_review": {"ZJ-R04", "ZJ-R05"},
    "zj_funds_lending_review": {"ZJ-R06", "ZJ-R07"},
    "zj_debt_service_review": {"ZJ-R08"},
}


def test_each_zj_workflow_contains_only_its_own_rules() -> None:
    from scripts.create_zj_review_sops import rule_sections

    assert RULES_PATH.is_file()
    sections = rule_sections()
    for skill_id, expected_rules in EXPECTED.items():
        content = json.loads((EXPORT_DIR / f"{skill_id}.json").read_text(encoding="utf-8"))
        SkillCard.model_validate(content)
        assert content["skill_id"] == skill_id
        actual_rules = {
            node["metadata"]["rule_id"]
            for node in content["nodes"]
            if "rule_id" in node["metadata"]
        }
        assert actual_rules == expected_rules
        assert len([node for node in content["nodes"] if "rule_id" in node["metadata"]]) == len(expected_rules)
        collect = content["nodes"][0]
        assert collect["result_outputs"] == [
            "attachment_role_map", "source_docs", "audit_meta",
        ]
        assert "以材料正文确认" in collect["instruction"]
        assert "来源索引" not in collect["instruction"]
        assert "路径" not in collect["instruction"]
        for review_node in [node for node in content["nodes"] if "rule_id" in node["metadata"]]:
            refs = review_node["capability_refs"]
            assert refs["required_knowledge_base_ids"] == []
            assert refs["knowledge_base_ids"] == []
            assert "knowledge_search" not in review_node["instruction"]
            assert "call_tool:knowledge_search" not in review_node["allowed_actions"]
            number = int(review_node["metadata"]["rule_id"][-2:])
            business_rule = "\n".join(
                line for line in sections[number][1].splitlines()
                if not line.startswith("检索关键词：")
            ).strip()
            assert business_rule in review_node["instruction"]
            assert "检索关键词：" not in review_node["instruction"]
            assert "槽位" not in review_node["instruction"]
            assert "read_file" not in review_node["instruction"]
            for other_number in range(1, 9):
                if other_number != number:
                    assert sections[other_number][1] not in review_node["instruction"]


def test_repayment_workflow_has_non_applicable_exit_without_a_pass_item() -> None:
    content = json.loads(
        (EXPORT_DIR / "zj_debt_service_review.json").read_text(encoding="utf-8")
    )
    edges = {(edge["source_node_id"], edge["next_node_id"]) for edge in content["edges"]}
    assert ("n1_gate", "n8_review") in edges
    assert ("n1_gate", "n2_not_applicable") in edges
    assert "n2_not_applicable" in content["terminal_node_ids"]
    skip = next(node for node in content["nodes"] if node["node_id"] == "n2_not_applicable")
    assert "不得生成判定项" in skip["instruction"]
    assert "协审报告" in skip["instruction"]


def test_zj_node_guidance_keeps_runtime_tool_protocol_out_of_business_instructions() -> None:
    for skill_id in EXPECTED:
        content = json.loads((EXPORT_DIR / f"{skill_id}.json").read_text(encoding="utf-8"))
        for node in content["nodes"]:
            instruction = node["instruction"]
            assert "mcp__staffdeck__" not in instruction
            assert "tool_id=" not in instruction
            assert "next_step_id=" not in instruction
            assert "slot_updates=" not in instruction
        aggregate = next(node for node in content["nodes"] if node["node_id"] == "n9_aggregate")
        assert aggregate["result_outputs"] == ["audit_payload"]
        assert "audit_payload" not in aggregate["instruction"]
        assert "风险 X、关注 Y、通过 Z" in aggregate["instruction"]
        for node in content["nodes"]:
            for field in [*node["result_inputs"], *node["result_outputs"]]:
                assert field not in node["instruction"]


def test_repayment_node_result_fields_are_configured_separately() -> None:
    content = json.loads(
        (EXPORT_DIR / "zj_debt_service_review.json").read_text(encoding="utf-8")
    )
    nodes = {node["node_id"]: node for node in content["nodes"]}
    assert nodes["n0_collect"]["result_outputs"] == [
        "attachment_role_map", "source_docs", "audit_meta",
    ]
    assert nodes["n1_gate"]["result_inputs"] == ["source_docs"]
    assert nodes["n1_gate"]["result_outputs"] == ["applicability"]
    assert nodes["n8_review"]["result_outputs"] == ["review_08"]
    assert nodes["n9_aggregate"]["result_inputs"] == ["audit_meta", "review_08"]
    assert nodes["n9_aggregate"]["result_outputs"] == ["audit_payload"]
    assert nodes["n10_save"]["result_outputs"] == ["save_result"]
    assert nodes["n11_report"]["result_outputs"] == ["report_url"]
