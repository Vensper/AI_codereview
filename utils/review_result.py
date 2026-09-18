import json
import re


MAX_COMMENTS = 10
SEVERITIES = {"low", "medium", "high"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def extract_json_object(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI output does not contain a JSON object.")
        value = json.loads(text[start : end + 1])

    if not isinstance(value, dict):
        raise ValueError("AI output root must be a JSON object.")
    return value


def normalize_review(value):
    if (
        not isinstance(value.get("summary"), str)
        or not value["summary"].strip()
        or not isinstance(value.get("change_intent"), str)
        or not isinstance(value.get("changes"), list)
        or not isinstance(value.get("comments"), list)
    ):
        raise ValueError("AI output is missing required review fields.")

    summary = str(value.get("summary", "")).strip()
    change_intent = str(value.get("change_intent", "")).strip()

    changes = []
    for item in value.get("changes", []):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        description = str(item.get("description", "")).strip()
        if path and description:
            changes.append({"path": path, "description": description})

    comments = []
    seen = set()
    for item in value.get("comments", [])[:MAX_COMMENTS]:
        if not isinstance(item, dict):
            continue

        path = str(item.get("path", "")).strip()
        side = str(item.get("side", "new")).strip().lower()
        severity = str(item.get("severity", "medium")).strip().lower()
        title = str(item.get("title", "代码评审建议")).strip()
        problem = str(item.get("problem", "")).strip()
        risk = str(item.get("risk", "")).strip()
        suggestion = str(item.get("suggestion", "")).strip()

        try:
            line = int(item.get("line", 0))
        except (TypeError, ValueError):
            line = 0

        if severity not in SEVERITIES:
            severity = "medium"
        if side not in {"new", "old"}:
            side = "new"

        key = (path, side, line)
        if key in seen:
            continue
        seen.add(key)

        comments.append(
            {
                "title": title or "代码评审建议",
                "path": path,
                "line": line,
                "side": side,
                "severity": severity,
                "problem": problem,
                "risk": risk,
                "suggestion": suggestion,
            }
        )

    comments.sort(
        key=lambda item: (
            SEVERITY_ORDER[item["severity"]],
            item["path"],
            item["line"],
        )
    )

    return {
        "summary": summary or "AI 未提供总体结论。",
        "change_intent": change_intent or "AI 未提供修改意图。",
        "changes": changes,
        "comments": comments,
    }


def inline_body(comment):
    return (
        f"**风险等级：{comment['severity'].capitalize()}**\n\n"
        f"**问题：{comment['title']}**\n\n"
        f"{comment['problem']}\n\n"
        f"**风险：** {comment['risk']}\n\n"
        f"**建议：** {comment['suggestion']}"
    )


def markdown_escape_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def fallback_section(review, fallback_items):
    if not fallback_items:
        return ""

    lines = [
        "## 无法行级定位的评审建议",
        "",
        "### 一、修改点分析",
        "",
        "| 文件 | 变更内容 |",
        "| --- | --- |",
    ]

    if review["changes"]:
        for change in review["changes"]:
            lines.append(
                f"| `{markdown_escape_cell(change['path'])}` | "
                f"{markdown_escape_cell(change['description'])} |"
            )
    else:
        lines.append("| 未识别 | AI 未返回结构化的修改点分析 |")

    lines.extend(
        [
            "",
            f"**修改意图：** {review['change_intent']}",
            "",
            "### 二、评审建议",
            "",
        ]
    )

    groups = [
        ("high", "1. 关键问题（必须修复）"),
        ("medium", "2. 重要建议（应该修复）"),
        ("low", "3. 小幅改进（考虑修复）"),
    ]
    for group_number, (severity, heading) in enumerate(groups, start=1):
        lines.append(f"#### {heading}")
        lines.append("")
        items = [
            item
            for item in fallback_items
            if item["comment"]["severity"] == severity
        ]
        if not items:
            lines.extend(["无。", ""])
            continue

        for index, item in enumerate(items, start=1):
            comment = item["comment"]
            location = (
                f"{comment['path']}:{comment['line']} ({comment['side']} side)"
                if comment["path"] and comment["line"] > 0
                else "无法确定"
            )
            lines.extend(
                [
                    f"##### {group_number}.{index} {comment['title']}",
                    "",
                    f"**文件位置：** `{location}`",
                    "",
                    f"**问题描述：** {comment['problem']}",
                    "",
                    f"**风险：** {comment['risk']}",
                    "",
                    f"**建议：** {comment['suggestion']}",
                    "",
                    f"> 降级原因：{item['reason']}",
                    "",
                ]
            )

    return "\n".join(lines).rstrip()


def build_summary_note(review, fallback_items, posted_count, build_number):
    counts = {severity: 0 for severity in SEVERITIES}
    for comment in review["comments"]:
        counts[comment["severity"]] += 1

    summary_lines = [
        "## 🤖 AI 代码审查汇总",
        "",
        "| 风险等级 | 数量 |",
        "| --- | ---: |",
        f"| High | {counts['high']} |",
        f"| Medium | {counts['medium']} |",
        f"| Low | {counts['low']} |",
        "",
        f"**总体结论：** {review['summary']}",
        "",
        f"- Jenkins 构建：#{build_number}",
        f"- 已发布行级评论：{posted_count}",
        f"- 已降级到汇总区：{len(fallback_items)}",
    ]

    fallback = fallback_section(review, fallback_items)
    lines = []
    if fallback:
        lines.extend([fallback, "", "---", ""])
    lines.extend(summary_lines)
    return "\n".join(lines).rstrip() + "\n"
