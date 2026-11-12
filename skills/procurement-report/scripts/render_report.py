"""Render the procurement compliance report from stdin JSON into results/*.html.

Packaged as a GeneralSkill file; executed via run_skill_script. The process cwd is
this script's directory, so the workspace root is derived from __file__ rather
than from cwd. stdout carries only a short status JSON, never the HTML body.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

_WARNING_RESULTS = {"预警", "关注", "不通过", "异常", "失败"}
_PLACEHOLDERS = ("{{TITLE}}", "{{SUMMARY}}", "{{ROWS}}", "{{FACTS}}", "{{SUGGESTION}}")


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _workspace_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".harness" / "skill-packages").is_dir():
            return parent
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".harness" / "skill-packages").is_dir():
            return parent
    return None


def _output_path(data: dict) -> str:
    raw = str(data.get("output_path") or "results/report.html").replace("\\", "/").lstrip("/")
    if raw.startswith("workspace/"):
        raw = raw[len("workspace/"):]
    if ".." in raw or not raw.startswith("results/") or not raw.endswith(".html"):
        raise ValueError("output_path 必须是 results/ 目录下的 .html 文件，且不能包含 ..")
    return raw


def _render_rows(items: object) -> str:
    if not isinstance(items, list):
        return ""
    rows = []
    for index, raw in enumerate(items, start=1):
        item = raw if isinstance(raw, dict) else {}
        result = str(item.get("result") or "")
        level = "warning" if result in _WARNING_RESULTS else "pass"
        rows.append(
            "<tr>"
            f"<td>{_escape(item.get('no') or index)}</td>"
            f"<td>{_escape(item.get('element') or '')}</td>"
            f"<td>{_escape(item.get('rule') or '')}</td>"
            f'<td class="{level}">{_escape(result)}</td>'
            "</tr>"
        )
    return "\n".join(rows)


def _render_facts(facts: object) -> str:
    if not isinstance(facts, list):
        return ""
    return "".join(f"<li>{_escape(fact)}</li>" for fact in facts)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        _emit({"ok": False, "error": f"stdin 不是合法 JSON：{exc}"})
        return 1
    if not isinstance(data, dict):
        _emit({"ok": False, "error": "stdin 必须是单个 JSON 对象"})
        return 1

    template = Path(__file__).resolve().parent.parent / "templates" / "report.html"
    if not template.is_file():
        _emit({"ok": False, "error": "包内模板 templates/report.html 不存在"})
        return 1

    try:
        relative = _output_path(data)
    except ValueError as exc:
        _emit({"ok": False, "error": str(exc)})
        return 1

    project = str(data.get("project") or "采购合规校验").strip()
    title = project if project.endswith("报告") else project + "报告"
    replacements = {
        "{{TITLE}}": _escape(title),
        "{{SUMMARY}}": _escape(data.get("summary") or ""),
        "{{ROWS}}": _render_rows(data.get("items")),
        "{{FACTS}}": _render_facts(data.get("facts")),
        "{{SUGGESTION}}": _escape(data.get("suggestion") or "无"),
    }
    rendered = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    if any(key in rendered for key in _PLACEHOLDERS):
        _emit({"ok": False, "error": "模板存在未替换的占位符"})
        return 1

    root = _workspace_root()
    output = (root / relative) if root else Path(relative)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        _emit({"ok": False, "error": f"写入报告失败：{exc}"})
        return 1

    _emit({"ok": True, "path": relative, "size": output.stat().st_size})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
