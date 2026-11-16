"""Translate harness slot assembly errors into actionable operator messages.

Management-side validation (``validate_published_sop`` / ``_validate_module_composition``)
runs the same compiler as runtime with ``strict=True``. When a SOP references a
capability the target Staff cannot currently use (unpublished general skill,
removed tool binding, ...), the raw error is an internal slot name plus a bare
resource id — a dead end for operators. This module resolves the resource row
and renders the fix path instead.
"""

from __future__ import annotations

from sqlmodel import Session

from staffdeck_harness.contracts.errors import RequiredSlotMissing, SlotNotBound

_TYPE_LABELS = {
    "general_skill": "通用技能",
    "tool": "工具",
    "knowledge_base": "知识库",
    "mcp_server": "MCP 服务",
}

_TYPE_MODELS = {
    "general_skill": "GeneralSkill",
    "tool": "Tool",
    "knowledge_base": "KnowledgeBase",
    "mcp_server": "MCPServer",
}


def describe_slot_error(
    db: Session,
    tenant_id: str,
    exc: Exception,
    *,
    sop_name: str | None = None,
    sop_id: str | None = None,
) -> str:
    """Render one slot assembly error as an operator-facing message.

    ``sop_name`` / ``sop_id`` identify the SOP (skill) being validated so the
    operator knows which process flow the failure belongs to.
    """

    sop_prefix = ""
    if sop_name or sop_id:
        title = f"「{sop_name}」" if sop_name else ""
        ref = f"（{sop_id}）" if sop_id else ""
        sop_prefix = f"SOP 流程{title}{ref}："

    if isinstance(exc, SlotNotBound):
        details = exc.details or {}
        resource_type = str(details.get("resource_type") or "")
        resource_id = str(details.get("resource") or "")
        label = _TYPE_LABELS.get(resource_type, "资源")
        name = _resource_display_name(db, tenant_id, resource_type, resource_id)
        shown = f"「{name}」（{resource_id}）" if name else f"（{resource_id}）"
        node = details.get("node_id")
        node_hint = f"（节点 {node!r}）" if node else ""
        return (
            f"{sop_prefix}槽位 {details.get('slot')!r}{node_hint} 绑定的{label}{shown}当前员工无法使用："
            f"请先为该员工启用对应{label}（员工配置 → 资源绑定；通用技能还需为已发布状态），"
            f"或移除 SOP 节点对该{label}的引用/必需标记后重试"
        )
    if isinstance(exc, RequiredSlotMissing):
        details = exc.details or {}
        node = details.get("node_id") or "未知节点"
        return (
            f"{sop_prefix}必填槽位 {details.get('slot')!r}（{node}）尚未绑定具体资源："
            f"请在该员工的 SOP 槽位绑定中指定资源，或将节点对应引用改为非必需"
        )
    return f"{sop_prefix}{exc}" if sop_prefix else str(exc)


def _resource_display_name(db: Session, tenant_id: str, resource_type: str, resource_id: str) -> str | None:
    model_name = _TYPE_MODELS.get(resource_type)
    if not model_name or not resource_id:
        return None
    from app.db import models as app_models

    model = getattr(app_models, model_name, None)
    if model is None:
        return None
    row = db.get(model, resource_id)
    if row is None or getattr(row, "tenant_id", None) != tenant_id:
        return None
    return getattr(row, "display_name", None) or getattr(row, "name", None) or resource_id
