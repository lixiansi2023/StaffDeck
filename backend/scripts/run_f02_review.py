#!/usr/bin/env python3
"""用 F02 用例端到端跑一次招采数字员工的点检 SOP。

把 input/用例A_F02非工程招标/ 下 5 份材料作为附件，发起一轮 chat turn，
由 planner 选中 f02_non_eng_bidding_review SOP（或用 forced_sop_id 强制），
跑完 12 节点后打印回复、事件轨迹与外部工具落库结果。

用法：
  python scripts/run_f02_review.py --dry-run            # 只预览将上传的材料与配置
  python scripts/run_f02_review.py                        # 实跑（需 HARNESS_V3_ROOT / 默认模型可用）
  python scripts/run_f02_review.py --forced              # 强制指定 SOP，跳过 planner 选择
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CASE_DIR = ROOT / "input" / "用例A_F02非工程招标"
SOP_SKILL_ID = "f02_non_eng_bidding_review"
DEFAULT_AGENT_ID = "agent_77affcb6b0de4e11"
TENANT = "tenant_demo"
MESSAGE = "请按 F02 规则对随附的采购文件进行点检，逐项给出结论并生成报告。"

for _p in (BACKEND, BACKEND / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# 引擎根目录：环境变量优先，否则仓库内 harness-engine
os.environ.setdefault("HARNESS_V3_ROOT", str(ROOT / "harness-engine"))

from sqlmodel import Session, select  # noqa: E402

from app.core.agent_loop import AgentLoop  # noqa: E402
from app.db.database import engine  # noqa: E402
from app.db.models import AgentEvent, Skill, User  # noqa: E402
from app.session.attachment_store import stage_chat_attachment  # noqa: E402
from app.session.attachments import parse_chat_attachment  # noqa: E402
from app.session.session_schema import ChatAttachmentRead, ChatTurnRequest  # noqa: E402


def load_case_files() -> list[Path]:
    files = sorted(p for p in CASE_DIR.iterdir() if p.is_file())
    if not files:
        raise SystemExit(f"用例目录为空或不存在：{CASE_DIR}")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="端到端跑 F02 点检 SOP")
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    parser.add_argument("--forced", action="store_true", help="强制指定 SOP（跳过 planner 选择）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不实际跑")
    args = parser.parse_args()

    files = load_case_files()
    print("用例材料：")
    for p in files:
        print("  -", p.name, "|", p.stat().st_size, "bytes")
    if args.dry_run:
        print("DRY-RUN：未发起对话。")
        print("  agent:", args.agent_id, "| forced_sop:", args.forced)
        print("  HARNESS_V3_ROOT:", os.environ.get("HARNESS_V3_ROOT"))
        return

    with Session(engine) as db:
        user = db.exec(select(User).where(User.tenant_id == TENANT)).first()
        if not user:
            raise SystemExit(f"租户 {TENANT} 下没有用户")
        skill = db.exec(
            select(Skill).where(Skill.tenant_id == TENANT, Skill.skill_id == SOP_SKILL_ID)
        ).first()
        if not skill:
            raise SystemExit(f"未找到 SOP {SOP_SKILL_ID}")

        # 附件：走平台标准 parse + stage 链路
        attachments: list[ChatAttachmentRead] = []
        for p in files:
            data = p.read_bytes()
            content_type = "application/json" if p.suffix == ".json" else "text/markdown"
            parsed = parse_chat_attachment(p.name, content_type, data)
            staged = stage_chat_attachment(parsed, data, tenant_id=TENANT, user_id=user.id)
            attachments.append(staged)
            print("已暂存附件:", p.name, "->", staged.id)

        req_kwargs = dict(
            tenant_id=TENANT, agent_id=args.agent_id, user_id=user.id,
            message=MESSAGE, channel="web",
            client_turn_id=f"f02-review-{uuid.uuid4().hex[:12]}",
            attachments=attachments,
        )
        if args.forced:
            req_kwargs["forced_sop_id"] = SOP_SKILL_ID
        req = ChatTurnRequest(**req_kwargs)

        print("\n发起对话轮次... client_turn_id =", req.client_turn_id)
        resp = AgentLoop(db).handle_turn(req)
        session_id = resp.session_id

        # 第一轮：附件读取 + n0 识别 + 尽可能多推进节点。
        # 上下文会在节点推进中累积，若本轮未跑完（queued / action_budget / 未到终点），
        # 用同一 session 发「继续」开启新 turn：新 turn 清空工具结果历史，
        # 但 session.slots_json 保留已完成的 review_items，从断点续跑。
        max_rounds = 8
        for round_no in range(1, max_rounds + 1):
            err = resp.runtime_error_code
            print(f"\n[轮 {round_no}] error={err} | reply={(resp.reply or '')[:120]}")
            if err is None or round_no == max_rounds:
                break
            # queued/预算/结果修正未完成 → 继续下一 turn
            cont = ChatTurnRequest(
                tenant_id=TENANT, agent_id=args.agent_id, user_id=user.id,
                session_id=session_id, message="继续完成点检并生成报告。", channel="web",
                client_turn_id=f"f02-review-cont-{round_no}-{uuid.uuid4().hex[:8]}",
            )
            resp = AgentLoop(db).handle_turn(cont)
            session_id = resp.session_id

        print("\n=== 最终回复 ===")
        print(resp.reply or "(空)")
        print("\n=== 运行元数据 ===")
        print("session_id:", session_id)
        print("runtime_error_code:", resp.runtime_error_code)

        events = db.exec(
            select(AgentEvent).where(AgentEvent.session_id == session_id)
        ).all()
        frames = [e for e in events if e.event_type == "task_frame_finished"]
        print("task frames:", len(frames))
        for e in frames[-5:]:
            payload = e.payload_json or {}
            print("  frame:", payload.get("status"), "| action_count:", payload.get("action_count"),
                  "| next:", payload.get("next_step_id"))


if __name__ == "__main__":
    main()
