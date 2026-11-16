#!/usr/bin/env python3
"""把合并流程规范注册为小明（agent_327b459bcfd84323）的「本地 SOP」。

本地 SOP 由三张表共同构成（参考 LocalSopSource / visible_published_skills）：
  1. skills              —— SOP 定义行（status='published'）
  2. agent_resource_bindings —— 把该 skill 绑定到 agent（status='active'）
  3. agent_skill_branches    —— 该 agent 的分支（source_skill_id 指向 skills.id，status='active'）

之前 merge_xiaoming_sops.py 只插入了第 3 张表，因此未被解析为本地 SOP。
本脚本补齐 1、2 并修正分支，使其真正出现在小明的本地 SOP 列表里。

直接读 SQLite（不经由公共 API）。幂等：已存在则更新到 active/synced/published。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

DB = os.path.join(os.path.dirname(__file__), "..", "skill_agent_loop.db")
AGENT_ID = "agent_327b459bcfd84323"
SKILL_ID = "merged_procurement_compliance_flow"
BRANCH_ID = f"agentbranch_{AGENT_ID}_{SKILL_ID}"

BINDING_META = {
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
BRANCH_META = dict(BINDING_META)


def now_iso() -> str:
    """库内既有 created_at/updated_at 均为 naive UTC（'%Y-%m-%d %H:%M:%S.%f'）。

    不能写带 '+00:00' 的 ISO 字符串：SQLModel 会解析成 offset-aware datetime，
    与既有 offset-naive 行混用时 `visible_skill_rows` 的 sorted(updated_at) 会抛
    TypeError: can't compare offset-naive and offset-aware datetimes。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def main() -> None:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    cur = c.cursor()

    # 取已落库的合并分支内容
    cur.execute(
        "SELECT content_json, tenant_id, head_version FROM agent_skill_branches WHERE id = ?",
        (BRANCH_ID,),
    )
    row = cur.fetchone()
    if not row:
        raise SystemExit("合并分支尚未落库，请先执行 merge_xiaoming_sops.py")
    content_json = row["content_json"]
    if isinstance(content_json, str):
        content_json = json.loads(content_json)
    tenant_id = row["tenant_id"]
    version = row["head_version"] or "1.0.0"

    content_json_str = json.dumps(content_json, ensure_ascii=False)
    name = content_json.get("name", "招采合规一体化全流程（合并）")
    business_domain = content_json.get("business_domain", "采购与供应链合规")
    description = content_json.get("description", "")

    # ---- 1) skills 定义行 ----
    cur.execute("SELECT id FROM skills WHERE tenant_id = ? AND skill_id = ?",
                (tenant_id, SKILL_ID))
    skill_row = cur.fetchone()
    if skill_row:
        skill_pk = skill_row["id"]
        cur.execute(
            "UPDATE skills SET version=?, name=?, business_domain=?, description=?, "
            "content_json=?, status='published', updated_at=? WHERE id=?",
            (version, name, business_domain, description, content_json_str, now_iso(), skill_pk),
        )
        print(f"已更新 skills 定义行: {skill_pk}")
    else:
        skill_pk = "skill_" + uuid.uuid4().hex
        cur.execute(
            "INSERT INTO skills (id, tenant_id, skill_id, version, name, business_domain, "
            "description, content_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?)",
            (skill_pk, tenant_id, SKILL_ID, version, name, business_domain,
             description, content_json_str, now_iso(), now_iso()),
        )
        print(f"已新建 skills 定义行: {skill_pk}")

    # ---- 2) agent_resource_bindings 绑定 ----
    cur.execute(
        "SELECT id FROM agent_resource_bindings WHERE tenant_id=? AND agent_id=? "
        "AND resource_type='skill' AND resource_id=?",
        (tenant_id, AGENT_ID, skill_pk),
    )
    bind_row = cur.fetchone()
    if bind_row:
        cur.execute(
            "UPDATE agent_resource_bindings SET status='active', metadata_json=?, updated_at=? "
            "WHERE id=?",
            (json.dumps(BINDING_META, ensure_ascii=False), now_iso(), bind_row["id"]),
        )
        print(f"已更新绑定: {bind_row['id']}")
    else:
        bind_id = "agentres_" + uuid.uuid4().hex
        cur.execute(
            "INSERT INTO agent_resource_bindings "
            "(id, tenant_id, agent_id, resource_type, resource_id, status, metadata_json, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, 'skill', ?, 'active', ?, ?, ?)",
            (bind_id, tenant_id, AGENT_ID, skill_pk,
             json.dumps(BINDING_META, ensure_ascii=False), now_iso(), now_iso()),
        )
        print(f"已新建绑定: {bind_id}")

    # ---- 3) 修正 agent_skill_branches ----
    cur.execute(
        "UPDATE agent_skill_branches SET source_skill_id=?, base_version=?, head_version=?, "
        "content_json=?, status='active', sync_state='synced', metadata_json=?, updated_at=? "
        "WHERE id=?",
        (skill_pk, version, version, content_json_str,
         json.dumps(BRANCH_META, ensure_ascii=False), now_iso(), BRANCH_ID),
    )
    print(f"已修正分支: {BRANCH_ID} (source_skill_id -> {skill_pk})")

    # ---- 3.2) 同步后台自动创建的 skill_versions 行的 content（避免残留旧 schema）----
    cur.execute("SELECT id FROM skill_versions WHERE tenant_id=? AND skill_id=? AND version=?",
                (tenant_id, SKILL_ID, version))
    for r in cur.fetchall():
        cur.execute("UPDATE skill_versions SET content_json=?, name=?, business_domain=?, "
                    "description=?, status='published', updated_at=? WHERE id=?",
                    (content_json_str, name, business_domain, description, now_iso(), r["id"]))
    cur.execute("UPDATE agent_skill_branch_versions SET content_json=?, updated_at=? "
                "WHERE tenant_id=? AND skill_id=? AND agent_id=? AND version=?",
                (content_json_str, now_iso(), tenant_id, SKILL_ID, AGENT_ID, version))
    print("已同步版本表 content")

    # ---- 3.5) 规范化各表相关行的时间戳（修复历史遗留的 offset-aware 值）----
    def naive(value: str) -> str:
        if not value:
            return now_iso()
        if "T" in value or "+" in value:
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.astimezone(timezone.utc).replace(tzinfo=None).strftime(
                    "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                return now_iso()
        return value

    for table, where, params in (
        ("skills", "id=?", (skill_pk,)),
        ("skill_versions", "skill_id=?", (SKILL_ID,)),
        ("agent_resource_bindings", "resource_type='skill' AND resource_id=?", (skill_pk,)),
        ("agent_skill_branches", "id=?", (BRANCH_ID,)),
        ("agent_skill_branch_versions", "skill_id=? AND agent_id=?", (SKILL_ID, AGENT_ID)),
    ):
        cur.execute(f"SELECT id, created_at, updated_at FROM {table} WHERE {where}", params)
        for r in cur.fetchall():
            cur.execute(
                f"UPDATE {table} SET created_at=?, updated_at=? WHERE id=?",
                (naive(r["created_at"]), naive(r["updated_at"]), r["id"]),
            )
    print("已规范化三张表时间戳为 naive UTC")

    c.commit()

    # ---- 校验：模拟 visible_published_skills 的可见性条件 ----
    cur.execute(
        "SELECT s.status AS skill_status, b.status AS bind_status, br.status AS branch_status "
        "FROM skills s "
        "JOIN agent_resource_bindings b ON b.resource_id=s.id "
        "JOIN agent_skill_branches br ON br.skill_id=s.skill_id AND br.agent_id=b.agent_id "
        "WHERE s.tenant_id=? AND s.skill_id=? AND b.agent_id=? AND b.resource_type='skill'",
        (tenant_id, SKILL_ID, AGENT_ID),
    )
    chk = cur.fetchone()
    c.close()
    ok = chk and chk["skill_status"] == "published" and chk["bind_status"] == "active" \
        and chk["branch_status"] == "active"
    print("\n可见性检查:", dict(chk) if chk else None,
          "✅ 已成为小明本地 SOP" if ok else "❌ 仍不可见")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
