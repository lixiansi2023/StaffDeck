#!/usr/bin/env python3
"""把 F02 非工程招标点检 SOP 完整导入到小明（agent_327b459bcfd84323）本地工作流。

复用 copy_sop_to_xiaoming.py 验证过的「生效 5 张表」范式：
  skills(published) + skill_versions(published)
  + agent_resource_bindings(active) + agent_skill_branches(active/synced)
  + agent_skill_branch_versions(active/synced)

与直接调 API 的区别：API publish 只保证招采数字员工自己的分支；
小明作为另一个员工，必须显式补上述关联行才可见。

内容源：backend/sop_exports/f02_non_eng_bidding_review.json（唯一真相源）。
幂等：已存在则更新，不重复插入。

已知坑（沿用已验证规避）：
  - capability_refs / retry_policy 等必须是 dict，不能是 list
  - created_at/updated_at 必须是 naive UTC
直接读 SQLite，不依赖 API Key。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from datetime import UTC, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "skill_agent_loop.db")
SOP_JSON = os.path.join(ROOT, "sop_exports", "f02_non_eng_bidding_review.json")

AGENT_ID = "agent_327b459bcfd84323"          # 小明
TENANT_ID = "tenant_demo"
SKILL_ID = "f02_non_eng_bidding_review"
NAME = "F02 非工程招标采购文件点检 SOP"
VERSION = "1.0.0"
BRANCH_ID = f"agentbranch_{AGENT_ID}_{SKILL_ID}"
BRANCH_VER_ID = f"agentbranchver_{AGENT_ID}_{SKILL_ID}_1_0_0"

META = {
    "created_by": "admin", "created_by_user_id": "distribution-admin",
    "created_by_username": "admin", "created_from_agent": True, "creator_name": "admin",
    "owner_agent_id": AGENT_ID, "owner_display_name": "Administrator",
    "owner_user_id": "distribution-admin", "owner_username": "admin",
    "created_by_display_name": "Administrator", "gallery_published_by": "distribution-admin",
    "scope": "agent_private", "visibility": "agent_private",
}
META_JSON = json.dumps(META, ensure_ascii=False)


def now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def load_content() -> dict:
    with open(SOP_JSON, encoding="utf-8") as fh:
        content = json.load(fh)
    # 修正可能的非法类型（同 copy_sop_to_xiaoming.py）
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
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from app.skills.skill_schema import SkillCard, skill_card_from_persisted

    content = load_content()
    SkillCard.model_validate(content)
    skill_card_from_persisted(content)
    print("SkillCard 校验: 通过")
    content_json = json.dumps(content, ensure_ascii=False)

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    cur = c.cursor()

    # ---- 1) skills 定义行（全局，置 published）----
    cur.execute("SELECT id FROM skills WHERE tenant_id=? AND skill_id=?", (TENANT_ID, SKILL_ID))
    row = cur.fetchone()
    if row:
        skill_pk = row["id"]
        cur.execute(
            "UPDATE skills SET version=?, name=?, business_domain=?, description=?, content_json=?, "
            "status='published', updated_at=? WHERE id=?",
            (VERSION, NAME, content.get("business_domain"), content.get("description", ""),
             content_json, now(), skill_pk),
        )
        print(f"更新 skills: {skill_pk} -> published")
    else:
        skill_pk = "skill_" + uuid.uuid4().hex
        cur.execute(
            "INSERT INTO skills (id,tenant_id,skill_id,version,name,business_domain,description,"
            "content_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'published',?,?)",
            (skill_pk, TENANT_ID, SKILL_ID, VERSION, NAME, content.get("business_domain"),
             content.get("description", ""), content_json, now(), now()),
        )
        print(f"新建 skills: {skill_pk}")

    # ---- 2) skill_versions（置 published）----
    cur.execute("SELECT id FROM skill_versions WHERE tenant_id=? AND skill_id=? AND version=?",
                (TENANT_ID, SKILL_ID, VERSION))
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE skill_versions SET name=?, business_domain=?, description=?, content_json=?, "
            "status='published', updated_at=? WHERE id=?",
            (NAME, content.get("business_domain"), content.get("description", ""),
             content_json, now(), row["id"]),
        )
    else:
        cur.execute(
            "INSERT INTO skill_versions (id,tenant_id,skill_id,version,name,business_domain,"
            "description,content_json,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,'published',?,?)",
            ("skillver_" + uuid.uuid4().hex, TENANT_ID, SKILL_ID, VERSION, NAME,
             content.get("business_domain"), content.get("description", ""), content_json, now(), now()),
        )
    print("写入 skill_versions -> published")

    # ---- 3) agent_resource_bindings（增量绑定小明，不动任何既有行）----
    cur.execute(
        "SELECT id,status FROM agent_resource_bindings WHERE tenant_id=? AND agent_id=? "
        "AND resource_type='skill' AND resource_id=?", (TENANT_ID, AGENT_ID, skill_pk)
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "UPDATE agent_resource_bindings SET status='active', metadata_json=?, updated_at=? WHERE id=?",
            (META_JSON, now(), row["id"]),
        )
        print("更新既有 F02 skill 绑定 -> active")
    else:
        cur.execute(
            "INSERT INTO agent_resource_bindings (id,tenant_id,agent_id,resource_type,resource_id,"
            "status,metadata_json,created_at,updated_at) VALUES (?,?,?,'skill',?,'active',?,?,?)",
            ("agentres_" + uuid.uuid4().hex, TENANT_ID, AGENT_ID, skill_pk, META_JSON, now(), now()),
        )
        print("新建 F02 skill 绑定 -> active")

    # ---- 4) agent_skill_branches ----
    cur.execute("SELECT id FROM agent_skill_branches WHERE id=?", (BRANCH_ID,))
    if cur.fetchone():
        cur.execute(
            "UPDATE agent_skill_branches SET source_skill_id=?, base_version=?, head_version=?, "
            "content_json=?, status='active', sync_state='synced', metadata_json=?, updated_at=? WHERE id=?",
            (skill_pk, VERSION, VERSION, content_json, META_JSON, now(), BRANCH_ID),
        )
    else:
        cur.execute(
            "INSERT INTO agent_skill_branches (id,tenant_id,agent_id,skill_id,source_skill_id,"
            "base_version,head_version,content_json,status,sync_state,metadata_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'active','synced',?,?,?)",
            (BRANCH_ID, TENANT_ID, AGENT_ID, SKILL_ID, skill_pk, VERSION, VERSION,
             content_json, META_JSON, now(), now()),
        )
    print(f"写入 agent_skill_branches: {BRANCH_ID} (active/synced)")

    # ---- 5) agent_skill_branch_versions ----
    cur.execute("SELECT id FROM agent_skill_branch_versions WHERE id=?", (BRANCH_VER_ID,))
    if cur.fetchone():
        cur.execute(
            "UPDATE agent_skill_branch_versions SET source_skill_id=?, content_json=?, status='active', "
            "sync_state='synced', updated_at=? WHERE id=?",
            (skill_pk, content_json, now(), BRANCH_VER_ID),
        )
    else:
        cur.execute(
            "INSERT INTO agent_skill_branch_versions (id,tenant_id,agent_id,skill_id,source_skill_id,"
            "version,base_version,content_json,status,sync_state,change_summary,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,'active','synced',?,?,?)",
            (BRANCH_VER_ID, TENANT_ID, AGENT_ID, SKILL_ID, skill_pk, VERSION, VERSION,
             content_json, "从招采数字员工 F02 SOP 导入小明", now(), now()),
        )
    print(f"写入 agent_skill_branch_versions: {BRANCH_VER_ID}")

    c.commit()

    # ---- 校验可见性（同 copy_sop_to_xiaoming.py）----
    cur.execute(
        "SELECT s.status skill_status, b.status bind_status, br.status branch_status, "
        "bv.status bver_status FROM skills s "
        "JOIN agent_resource_bindings b ON b.resource_id=s.id AND b.agent_id=? "
        "JOIN agent_skill_branches br ON br.skill_id=s.skill_id AND br.agent_id=b.agent_id "
        "JOIN agent_skill_branch_versions bv ON bv.skill_id=s.skill_id AND bv.agent_id=b.agent_id "
        "WHERE s.tenant_id=? AND s.skill_id=?", (AGENT_ID, TENANT_ID, SKILL_ID),
    )
    chk = cur.fetchone()
    c.close()
    ok = bool(chk) and all(chk[k] in ("active", "published") for k in
                           ("skill_status", "bind_status", "branch_status", "bver_status"))
    print("\n可见性校验:", dict(chk) if chk else None)
    print("结果:", "已保存到小明本地工作流（5 张关联表齐全）" if ok else "未生效")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
