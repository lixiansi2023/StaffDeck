#!/usr/bin/env python3
"""把「F02 非工程招标点检 SOP」从 SkillCard JSON 创建到指定 agent 并发布。

草稿内容来自 backend/sop_exports/f02_non_eng_bidding_review.json，
目标 agent 默认 agent_77affcb6b0de4e11（招采数字员工）。

两种落库方式：
  1) 公共 API（默认，复用 create_validation_sop.py 的模板）：
     POST /api/v1/agents/{agent_id}/sops          创建草稿
     POST /api/v1/sops/{sop_id}:publish           发布
     鉴权 Bearer API Key（sd_...），需 sops:write / sops:publish 作用域。
  2) 本地 SQLite（--local，无 API Key 时用）：
     直接调用 app.api.skills 的 create_skill / publish_skill，复用全部校验与绑定逻辑。

发布前会自动把 SOP 引用的工具绑定到目标员工（含 agent_private provenance），
并按 ADR-0001 D4 把 harness_max_actions 调到 64。重跑走 update_skill 幂等更新。

用法：
  python scripts/create_f02_review_sop.py --dry-run
  python scripts/create_f02_review_sop.py --api-key sd_live_xxx --publish
  python scripts/create_f02_review_sop.py --local --publish --set-max-actions 64

环境变量回退：STAFFDECK_API_KEY / STAFFDECK_BASE_URL / STAFFDECK_AGENT_ID
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

BACKEND = Path(__file__).resolve().parents[1]
SOP_JSON = BACKEND / "sop_exports" / "f02_non_eng_bidding_review.json"
DEFAULT_AGENT_ID = "agent_77affcb6b0de4e11"
DEFAULT_BASE_URL = "http://localhost:8000/api/v1"

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
from app.api.skills import (
    create_skill,
    publish_skill,
    update_skill,
)
from app.db.database import engine
from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    Skill,
    User,
    utc_now,
)
from app.skills.skill_schema import (
    SkillCard,
    SkillCreateRequest,
    SkillUpdateRequest,
    skill_card_from_persisted,
)


def load_sop_content() -> dict[str, Any]:
    with SOP_JSON.open(encoding="utf-8") as fh:
        content = json.load(fh)
    card = SkillCard.model_validate(content)
    return card.model_dump()


# ---------------------------------------------------------------------------
# 公共 API 路径
# ---------------------------------------------------------------------------
def _request(method: str, url: str, *, headers: dict[str, str], body: Any | None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code} {method} {url}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"无法连接 {url}: {exc.reason}") from exc


def run_api(content: dict[str, Any], agent_id: str, base_url: str, api_key: str, publish: bool,
            idempotency_key: str | None) -> None:
    base = base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Idempotency-Key": idempotency_key or f"sop-{content['skill_id']}",
    }
    url = f"{base}/agents/{agent_id}/sops"
    print(f"POST {url}")
    created = _request("POST", url, headers=headers, body={"content": content})
    draft_id = created.get("id")
    sop_id = created.get("sop_id") or created.get("skill_id")
    print("创建成功：", json.dumps({
        "skill_id": created.get("skill_id"), "id": draft_id,
        "sop_id": sop_id, "status": created.get("status"),
    }, ensure_ascii=False))

    if publish:
        if not (draft_id and sop_id):
            raise SystemExit("响应缺少 id / sop_id，无法发布。")
        pub_url = f"{base}/sops/{sop_id}:publish?agent_id={agent_id}"
        print(f"POST {pub_url}")
        published = _request("POST", pub_url,
                             headers={"Authorization": headers["Authorization"]},
                             body={"draft_id": draft_id})
        print("发布成功：status =", published.get("status"))
    else:
        print("\n草稿已落库，未发布。确认后执行：")
        print(f"  curl -X POST '{base}/sops/{sop_id}:publish?agent_id={agent_id}' \\")
        print(f"    -H 'Authorization: Bearer {api_key}' -H 'Content-Type: application/json' \\")
        print(f"    -d '{{\"draft_id\": \"{draft_id}\"}}'")


# ---------------------------------------------------------------------------
# 本地 SQLite 路径
# ---------------------------------------------------------------------------
def run_local(content: dict[str, Any], agent_id: str, username: str, publish: bool,
              set_max_actions: int | None) -> None:
    session = Session(engine)
    admin = session.exec(select(User).where(User.username == username)).first()
    if not admin:
        raise SystemExit(f"未找到用户 '{username}'，请检查用户名。")
    tenant_id = admin.tenant_id
    print(f"以用户 {admin.username}（tenant={tenant_id}）向 agent {agent_id} 落库 SOP。")

    # 发布前：确保 SOP 引用的工具已绑定到该员工且带 agent_private provenance，
    # 否则 CompositionCompiler 的 visible_resources 不含它们，发布报 SlotNotBound。
    tool_ids = sorted({
        str(tid)
        for node in content["nodes"]
        for tid in (node.get("capability_refs") or {}).get("tool_ids", [])
    })
    all_bindings = session.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
        )
    ).all()
    reference_meta: dict[str, Any] = {}
    for b in all_bindings:
        if b.resource_type == "tool" and b.status == "active" and (b.metadata_json or {}).get("scope") == "agent_private":
            reference_meta = dict(b.metadata_json or {})
            break
    resources = [
        AgentResourceBindingInput(
            resource_type=cast(AgentResourceType, b.resource_type), resource_id=b.resource_id,
            status=cast(Any, b.status), metadata=dict(b.metadata_json or {}),
        )
        for b in all_bindings if b.status in ("active", "inactive")
    ]
    have = {(r.resource_type, r.resource_id) for r in resources}
    for tid in tool_ids:
        if ("tool", tid) not in have:
            meta = {**reference_meta, "owner_agent_id": agent_id}
            resources.append(AgentResourceBindingInput(resource_type="tool", resource_id=tid,
                                                        status="active", metadata=meta))
            print("新增工具绑定:", tid)
        else:
            b = next(b for b in all_bindings if b.resource_type == "tool" and b.resource_id == tid)
            meta = {**reference_meta, **(b.metadata_json or {}), "owner_agent_id": agent_id}
            if (b.metadata_json or {}).get("scope") != "agent_private":
                b.metadata_json = meta
                b.updated_at = utc_now()
                session.add(b)
                print("补齐工具 agent_private 元数据:", tid)
    if tool_ids:
        update_agent_resources(
            agent_id, AgentResourcesUpdateRequest(tenant_id=tenant_id, resources=resources),
            db=session, current_user=admin,
        )
        session.commit()
        print("工具绑定就绪。")

    if set_max_actions is not None:
        agent = session.get(AgentProfile, agent_id)
        if agent is None:
            raise SystemExit(f"未找到 agent {agent_id}，无法设置预算。")
        agent.harness_max_actions = max(1, min(int(set_max_actions), 100))
        session.add(agent)
        session.commit()
        print(f"已设置 {agent_id}.harness_max_actions = {agent.harness_max_actions}")

    skill_id = str(content["skill_id"])
    existing = session.exec(
        select(Skill).where(Skill.tenant_id == tenant_id, Skill.skill_id == skill_id)
    ).first()
    if existing:
        current = skill_card_from_persisted(existing.content_json).model_dump()
        if current == content:
            print(f"已存在且内容一致，跳过更新：skill_id={skill_id}")
            created = existing
        else:
            try:
                created = update_skill(
                    skill_id, SkillUpdateRequest(tenant_id=tenant_id, content=SkillCard.model_validate(content)),
                    agent_id=agent_id, db=session, current_user=admin,
                )
                print(f"已存在，更新草稿：skill_id={created.skill_id} status={created.status}")
            except Exception as exc:
                session.rollback()
                raise SystemExit(f"更新失败：{type(exc).__name__}: {exc}") from exc
    else:
        try:
            request = SkillCreateRequest(
                tenant_id=tenant_id, content=SkillCard.model_validate(content), status="draft",
            )
            created = create_skill(request, agent_id=agent_id, db=session, current_user=admin)
        except Exception as exc:
            session.rollback()
            raise SystemExit(f"创建失败：{type(exc).__name__}: {exc}") from exc
        print(f"创建成功：skill_id={created.skill_id} id={created.id} status={created.status}")

    if publish:
        try:
            published = publish_skill(
                created.skill_id, tenant_id=tenant_id, agent_id=agent_id,
                db=session, current_user=admin,
            )
        except Exception as exc:
            session.rollback()
            raise SystemExit(f"发布失败：{type(exc).__name__}: {exc}") from exc
        print(f"已发布：status = {published.status}")

    session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="创建并可选发布 F02 非工程招标点检 SOP")
    parser.add_argument("--agent-id", default=os.getenv("STAFFDECK_AGENT_ID", DEFAULT_AGENT_ID),
                        help=f"目标 agent id（默认 {DEFAULT_AGENT_ID}）")
    parser.add_argument("--api-key", default=os.getenv("STAFFDECK_API_KEY"),
                        help="公共 API Bearer Key sd_...（env: STAFFDECK_API_KEY）")
    parser.add_argument("--base-url", default=os.getenv("STAFFDECK_BASE_URL", DEFAULT_BASE_URL),
                        help=f"公共 API 基址（默认 {DEFAULT_BASE_URL}）")
    parser.add_argument("--idempotency-key", default=None, help="幂等键，默认用 skill_id")
    parser.add_argument("--local", action="store_true", help="本地 SQLite 落库（无需 API Key）")
    parser.add_argument("--username", default="admin", help="本地落库使用的 admin 用户名")
    parser.add_argument("--set-max-actions", type=int, default=None,
                        help="ADR-0001 D4：把 agent 的 harness_max_actions 设为该值（如 64，上限 100）")
    parser.add_argument("--publish", action="store_true", help="创建后直接发布")
    parser.add_argument("--dry-run", action="store_true", help="只做 schema 校验并预览摘要，不落库")
    args = parser.parse_args()

    content = load_sop_content()
    print(
        f"SkillCard 校验通过：{content['skill_id']} v{content['version']} "
        f"| nodes={len(content['nodes'])} edges={len(content['edges'])} | agent={args.agent_id}"
    )
    if args.dry_run:
        print("dry-run：未落库。")
        return

    if args.local:
        run_local(content, args.agent_id, args.username, args.publish, args.set_max_actions)
    else:
        if not args.api_key:
            raise SystemExit("公共 API 需要 --api-key（或 STAFFDECK_API_KEY）；无 Key 请加 --local。")
        run_api(content, args.agent_id, args.base_url, args.api_key, args.publish, args.idempotency_key)


if __name__ == "__main__":
    main()
