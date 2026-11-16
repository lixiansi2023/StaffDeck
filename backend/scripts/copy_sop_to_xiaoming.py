#!/usr/bin/env python3
"""把「用户修改后的流程」复制一份，命名为「建设工程招标文件呈批表」，
注册为小明（agent_327b459bcfd84323）的本地 SOP。

内容源：优先取最新修改的分支 v1.3.0「非工程招标采购文件呈批表」
（agent_77affcb6b0de4e11 / agentbranch_a3faf2b6efd54645）；
它与小明分支 v1.1.0 的节点、边、起终点完全一致，仅名称与版本不同。

本地 SOP 规范（与 LocalSopSource / visible_published_skills 对齐）：
  skills(published) + skill_versions + agent_resource_bindings(active)
  + agent_skill_branches(active/synced) + agent_skill_branch_versions

已知坑（本脚本已规避）：
  - capability_refs / retry_policy 必须是 dict，不能是 list/tuple
  - created_at/updated_at 必须是 naive UTC（'%Y-%m-%d %H:%M:%S.%f'），
    否则 sorted(updated_at) 抛 offset-naive/aware TypeError

直接读 SQLite，幂等执行（已存在则更新，不重复插入）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "skill_agent_loop.db")

AGENT_ID = "agent_327b459bcfd84323"          # 小明
TENANT_ID = "tenant_demo"
TARGET_SKILL_ID = "construction_bid_approval_form"
TARGET_NAME = "建设工程招标文件呈批表"
TARGET_VERSION = "1.0.0"
BRANCH_ID = f"agentbranch_{AGENT_ID}_{TARGET_SKILL_ID}"

# 源分支（按更新时间倒序，取第一个可用的 12 节点修改版）
SOURCE_BRANCH_IDS = (
    "agentbranch_a3faf2b6efd54645",  # 招采数字员工 v1.3.0「非工程招标采购文件呈批表」
    f"agentbranch_{AGENT_ID}_merged_procurement_compliance_flow",  # 小明 v1.1.0
)

META = {
    "created_by": "admin",
    "created_by_user_id": "distribution-admin",
    "created_by_username": "admin",
    "created_from_agent": True,
    "creator_name": "admin",
    "owner_agent_id": AGENT_ID,
    "owner_display_name": "Administrator",
    "owner_user_id": "distribution-admin",
    "owner_username": "admin",
    "scope": "agent_private",
    "visibility": "agent_private",
    "created_by_display_name": "Administrator",
    "gallery_published_by": "distribution-admin",
}
META_JSON = json.dumps(META, ensure_ascii=False)


def now() -> str:
    """库内统一的 naive UTC 时间戳格式。"""
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def load_source(cur: sqlite3.Cursor) -> tuple[dict, str]:
    for bid in SOURCE_BRANCH_IDS:
        cur.execute("SELECT content_json, head_version FROM agent_skill_branches WHERE id=?", (bid,))
        row = cur.fetchone()
        if not row:
            continue
        obj = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        if obj.get("nodes"):
            print(f"内容源: {bid} (name={obj.get('name')}, version={row[1]}, "
                  f"{len(obj['nodes'])} 节点 / {len(obj.get('edges', []))} 边)")
            return obj, bid
    raise SystemExit("未找到修改后的流程分支")


def build_content(src: dict) -> dict:
    content = json.loads(json.dumps(src, ensure_ascii=False))  # 深拷贝
    content["skill_id"] = TARGET_SKILL_ID
    content["name"] = TARGET_NAME
    content["version"] = TARGET_VERSION
    # 修正源节点里可能存在的非法类型，避免 SkillCard(extra=forbid) 校验失败
    for n in content.get("nodes", []):
        for fld, default in (
            ("capability_refs", {}), ("retry_policy", {}), ("knowledge_scope", {}),
            ("metadata", {}), ("expected_user_info", []), ("allowed_actions", []),
        ):
            if not isinstance(n.get(fld), type(default)):
                n[fld] = default
        if not isinstance(n.get("optional"), bool):
            n["optional"] = False
    return content


def main() -> None:
    if not os.path.exists(DB):
        raise SystemExit(f"数据库不存在: {DB}")

    # 先用项目 schema 校验生成的 content
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    try:
        from app.skills.skill_schema import SkillCard, skill_card_from_persisted
    except ImportError:
        SkillCard = None
        skill_card_from_persisted = None

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    cur = c.cursor()

    src, src_branch = load_source(cur)
    content = build_content(src)
    content_json = json.dumps(content, ensure_ascii=False)

    if SkillCard is not None:
        SkillCard.model_validate(content)
        skill_card_from_persisted(content)
        print("SkillCard 校验: ✅ 通过")
    else:
        print("SkillCard 校验: ⚠ 跳过（无法 import app）")

    # ---- 1) skills 定义行 ----
    cur.execute("SELECT id FROM skills WHERE tenant_id=? AND skill_id=?",
                (TENANT_ID, TARGET_SKILL_ID))
    row = cur.fetchone()
    if row:
        skill_pk = row["id"]
        cur.execute(
            "UPDATE skills SET version=?, name=?, business_domain=?, description=?, "
            "content_json=?, status='published', updated_at=? WHERE id=?",
            (TARGET_VERSION, TARGET_NAME, content.get("business_domain"),
             content.get("description", ""), content_json, now(), skill_pk),
        )
        print(f"已更新 skills: {skill_pk}")
    else:
        skill_pk = "skill_" + uuid.uuid4().hex
        cur.execute(
            "INSERT INTO skills (id, tenant_id, skill_id, version, name, business_domain, "
            "description, content_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)",
            (skill_pk, TENANT_ID, TARGET_SKILL_ID, TARGET_VERSION, TARGET_NAME,
             content.get("business_domain"), content.get("description", ""),
             content_json, now(), now()),
        )
        print(f"已新建 skills: {skill_pk}")

    # ---- 2) skill_versions ----
    cur.execute("SELECT id FROM skill_versions WHERE tenant_id=? AND skill_id=? AND version=?",
                (TENANT_ID, TARGET_SKILL_ID, TARGET_VERSION))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE skill_versions SET name=?, business_domain=?, description=?, "
                    "content_json=?, status='published', updated_at=? WHERE id=?",
                    (TARGET_NAME, content.get("business_domain"), content.get("description", ""),
                     content_json, now(), row["id"]))
    else:
        cur.execute(
            "INSERT INTO skill_versions (id, tenant_id, skill_id, version, name, "
            "business_domain, description, content_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)",
            ("skillver_" + uuid.uuid4().hex, TENANT_ID, TARGET_SKILL_ID, TARGET_VERSION,
             TARGET_NAME, content.get("business_domain"), content.get("description", ""),
             content_json, now(), now()),
        )
    print("已写入 skill_versions")

    # ---- 3) agent_resource_bindings（绑定到小明）----
    cur.execute(
        "SELECT id FROM agent_resource_bindings WHERE tenant_id=? AND agent_id=? "
        "AND resource_type='skill' AND resource_id=?",
        (TENANT_ID, AGENT_ID, skill_pk),
    )
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE agent_resource_bindings SET status='active', metadata_json=?, "
                    "updated_at=? WHERE id=?", (META_JSON, now(), row["id"]))
    else:
        cur.execute(
            "INSERT INTO agent_resource_bindings (id, tenant_id, agent_id, resource_type, "
            "resource_id, status, metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'skill', ?, 'active', ?, ?, ?)",
            ("agentres_" + uuid.uuid4().hex, TENANT_ID, AGENT_ID, skill_pk,
             META_JSON, now(), now()),
        )
    print("已写入 agent_resource_bindings（绑定小明 active）")

    # ---- 4) agent_skill_branches ----
    cur.execute("SELECT id FROM agent_skill_branches WHERE id=?", (BRANCH_ID,))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE agent_skill_branches SET source_skill_id=?, base_version=?, head_version=?, "
            "content_json=?, status='active', sync_state='synced', metadata_json=?, "
            "updated_at=? WHERE id=?",
            (skill_pk, TARGET_VERSION, TARGET_VERSION, content_json, META_JSON, now(), BRANCH_ID),
        )
    else:
        cur.execute(
            "INSERT INTO agent_skill_branches (id, tenant_id, agent_id, skill_id, "
            "source_skill_id, base_version, head_version, content_json, status, sync_state, "
            "metadata_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'synced', ?, ?, ?)",
            (BRANCH_ID, TENANT_ID, AGENT_ID, TARGET_SKILL_ID, skill_pk,
             TARGET_VERSION, TARGET_VERSION, content_json, META_JSON, now(), now()),
        )
    print(f"已写入 agent_skill_branches: {BRANCH_ID}")

    # ---- 5) agent_skill_branch_versions（初始版本行）----
    cur.execute(
        "SELECT id FROM agent_skill_branch_versions WHERE tenant_id=? AND agent_id=? "
        "AND skill_id=? AND version=?",
        (TENANT_ID, AGENT_ID, TARGET_SKILL_ID, TARGET_VERSION),
    )
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE agent_skill_branch_versions SET content_json=?, status='active', "
                    "sync_state='synced', updated_at=? WHERE id=?",
                    (content_json, now(), row["id"]))
    else:
        cur.execute(
            "INSERT INTO agent_skill_branch_versions (id, tenant_id, agent_id, skill_id, "
            "source_skill_id, version, base_version, content_json, status, sync_state, "
            "change_summary, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'synced', ?, ?, ?)",
            ("agentbranchver_" + uuid.uuid4().hex, TENANT_ID, AGENT_ID, TARGET_SKILL_ID,
             skill_pk, TARGET_VERSION, TARGET_VERSION, content_json,
             "从修改后的流程复制", now(), now()),
        )
    print("已写入 agent_skill_branch_versions")

    c.commit()

    # ---- 校验可见性 ----
    cur.execute(
        "SELECT s.status AS skill_status, b.status AS bind_status, br.status AS branch_status, "
        "s.name AS name FROM skills s "
        "JOIN agent_resource_bindings b ON b.resource_id=s.id "
        "JOIN agent_skill_branches br ON br.skill_id=s.skill_id AND br.agent_id=b.agent_id "
        "WHERE s.tenant_id=? AND s.skill_id=? AND b.agent_id=? AND b.resource_type='skill'",
        (TENANT_ID, TARGET_SKILL_ID, AGENT_ID),
    )
    chk = cur.fetchone()
    c.close()
    ok = bool(chk) and chk["skill_status"] == "published" and chk["bind_status"] == "active" \
        and chk["branch_status"] == "active"
    print("\n可见性:", dict(chk) if chk else None,
          "✅ 已保存到小明本地 SOP" if ok else "❌ 未生效")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
