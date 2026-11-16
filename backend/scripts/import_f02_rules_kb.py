#!/usr/bin/env python3
"""把 F02 点检规则文档导入知识库、绑定到招采数字员工，并回填到 SOP 节点后重发布。

幂等：按 (tenant_id, KB 名) 复用已存在的知识库；文档按文件名去重，不重复导入。
本地 SQLite 直连，无 API Key 依赖；导入走 KnowledgeService.create_ingest_job +
_run_ingest_job，无 LLM 配置也能成功（分桶走本地降级）。

用法：
  python scripts/import_f02_rules_kb.py            # 建库/导入/绑定/回填/重发布
  python scripts/import_f02_rules_kb.py --dry-run  # 只预览，不落库
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SOP_JSON = BACKEND / "sop_exports" / "f02_non_eng_bidding_review.json"
RULES_MD = ROOT / "docs" / "seeds" / "规则库_F02非工程招标点检.md"
KB_NAME = "规则-非工程招标点检 F02"
DEFAULT_AGENT_ID = "agent_77affcb6b0de4e11"
CHECK_NODE_IDS = [f"n{i}_" for i in range(1, 9)]

for _p in (BACKEND, BACKEND / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sqlmodel import Session, select

from app.agents.schema import (
    AgentResourceBindingInput,
    AgentResourcesUpdateRequest,
    AgentResourceType,
)
from app.api.agents import update_agent_resources
from app.api.skills import publish_skill, update_skill
from app.db.database import engine
from app.db.models import (
    AgentResourceBinding,
    KnowledgeBase,
    KnowledgeDocument,
    Skill,
    User,
)
from app.knowledge.service import IngestPayload, KnowledgeService
from app.skills.skill_schema import (
    SkillCard,
    SkillUpdateRequest,
    skill_card_from_persisted,
)


def load_sop() -> dict:
    return json.loads(SOP_JSON.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 F02 规则知识库并回填 SOP")
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID, help="目标 agent id")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不落库")
    args = parser.parse_args()

    if not RULES_MD.exists():
        raise SystemExit(f"规则文档不存在：{RULES_MD}")
    sop = load_sop()
    if args.dry_run:
        print("DRY-RUN")
        print("  KB name:", KB_NAME)
        print("  doc:", RULES_MD, "| bytes:", RULES_MD.stat().st_size)
        print("  agent:", args.agent_id)
        print("  nodes to backfill:", [n["node_id"] for n in sop["nodes"]
                                        if any(n["node_id"].startswith(p) for p in CHECK_NODE_IDS)])
        return

    with Session(engine) as db:
        admin = db.exec(select(User).where(User.username == "admin")).first()
        if not admin:
            raise SystemExit("未找到 admin 用户")
        tenant = admin.tenant_id

        # 1) 建库（按名字幂等）
        kb = db.exec(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == tenant, KnowledgeBase.name == KB_NAME
            )
        ).first()
        if kb:
            print("复用知识库:", kb.id)
        else:
            kb = KnowledgeBase(
                tenant_id=tenant, name=KB_NAME,
                description="F02 非工程招标采购文件点检的 8 条审核要素判定规则（含附件角色识别与结论口径）。",
                capability_scope="sop_specific", status="active",
                metadata_json={"rule_lib_version": "V1.0", "form_code": "F02",
                               "source": "docs/seeds/规则库_F02非工程招标点检.md"},
            )
            db.add(kb)
            db.commit()
            db.refresh(kb)
            print("新建知识库:", kb.id)

        # 2) 导入文档（按文件名去重）
        doc_name = RULES_MD.name
        existing_doc = db.exec(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == tenant,
                KnowledgeDocument.knowledge_base_id == kb.id,
                KnowledgeDocument.filename == doc_name,
            )
        ).first()
        if existing_doc:
            print("文档已存在，跳过导入:", existing_doc.id, "| status:", existing_doc.status)
            doc = existing_doc
        else:
            svc = KnowledgeService(db)
            job = svc.create_ingest_job(IngestPayload(
                tenant_id=tenant,
                knowledge_base_id=kb.id,
                filename=doc_name,
                content_base64=base64.b64encode(RULES_MD.read_bytes()).decode("ascii"),
                title="F02 非工程招标点检规则库",
                metadata={"rule_lib_version": "V1.0"},
            ))
            svc._run_ingest_job(job.id)
            doc = db.exec(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.tenant_id == tenant,
                    KnowledgeDocument.knowledge_base_id == kb.id,
                    KnowledgeDocument.filename == doc_name,
                )
            ).first()
            if not doc:
                raise SystemExit(f"导入后未找到文档：job={job.id} status={job.status}")
            print("文档导入完成:", doc.id, "| status:", doc.status)

        chunk_count = db.exec(
            select(KnowledgeDocument.chunk_count).where(KnowledgeDocument.id == doc.id)
        ).first()
        print("文档分块数:", chunk_count)

        # 3) 绑定知识库到 agent（全量替换，保留既有绑定）
        bindings = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == tenant,
                AgentResourceBinding.agent_id == args.agent_id,
            )
        ).all()
        resources = [
            AgentResourceBindingInput(
                resource_type=cast_type(b.resource_type), resource_id=b.resource_id,
                status="active" if b.status == "active" else "inactive",
                metadata=dict(b.metadata_json or {}),
            )
            for b in bindings if b.status in ("active", "inactive")
        ]
        kb_bound = any(r.resource_id == kb.id for r in resources)
        if not kb_bound:
            # 复用已有 KB 绑定的 provenance 元数据
            ref_meta = {}
            for b in bindings:
                if b.resource_type == "knowledge_base" and (b.metadata_json or {}).get("scope") == "agent_private":
                    ref_meta = dict(b.metadata_json or {})
                    break
            meta = {**ref_meta, "owner_agent_id": args.agent_id, "created_from_agent": True}
            resources.append(AgentResourceBindingInput(
                resource_type="knowledge_base", resource_id=kb.id, status="active", metadata=meta,
            ))
            update_agent_resources(
                args.agent_id, AgentResourcesUpdateRequest(tenant_id=tenant, resources=resources),
                db=db, current_user=admin,
            )
            db.commit()
            print("知识库已绑定到 agent:", kb.id)
        else:
            print("知识库已绑定，跳过:", kb.id)

        # 4) 回填 SOP 的 n1-n8 knowledge_base_ids
        changed = False
        for node in sop["nodes"]:
            if any(node["node_id"].startswith(p) for p in CHECK_NODE_IDS):
                refs = node.setdefault("capability_refs", {})
                if kb.id not in refs.get("knowledge_base_ids", []):
                    refs["knowledge_base_ids"] = [kb.id]
                    refs["required_knowledge_base_ids"] = [kb.id]
                    changed = True
        if changed:
            SOP_JSON.write_text(json.dumps(sop, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("已回填 SOP 节点 knowledge_base_ids:", kb.id)
        else:
            print("SOP 节点已回填，跳过")

        # 5) 重发布（内容变更才更新）
        skill_id = sop["skill_id"]
        existing = db.exec(
            select(Skill).where(Skill.tenant_id == tenant, Skill.skill_id == skill_id)
        ).first()
        new_card = SkillCard.model_validate(sop)
        if existing:
            current = skill_card_from_persisted(existing.content_json).model_dump()
            if current != new_card.model_dump():
                update_skill(
                    skill_id, SkillUpdateRequest(tenant_id=tenant, content=new_card),
                    agent_id=args.agent_id, db=db, current_user=admin,
                )
                print("SOP 内容已更新")
        published = publish_skill(skill_id, tenant_id=tenant, agent_id=args.agent_id,
                                  db=db, current_user=admin)
        print("SOP 已重发布:", skill_id, "| status:", published.status)
        print("完成：KB=", kb.id, "| DOC=", doc.id, "| 规则版本=V1.0")


def cast_type(value: str):
    from typing import cast


    return cast(AgentResourceType, value)


if __name__ == "__main__":
    main()
