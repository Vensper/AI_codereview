import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
MAX_DIFF_CHARS = 60000
MAX_COMMENTS = 10
SEVERITIES = {"low", "medium", "high"}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not available.")
    return value


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def write_text(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def write_json(path, value):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def deepseek_request(payload, api_key):
    request = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    last_error = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = RuntimeError(f"DeepSeek API returned HTTP {exc.code}.")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise last_error from exc
        except urllib.error.URLError as exc:
            last_error = RuntimeError(
                f"Cannot connect to DeepSeek API: {exc.reason}"
            )
            if attempt == 3:
                raise last_error from exc
        time.sleep(attempt * 2)
    raise last_error


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


def clean_diff_path(value):
    value = value.strip()
    if value in {"/dev/null", "dev/null"}:
        return "/dev/null"
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def parse_changed_lines(diff_text):
    """Return exact changed-line positions keyed by (path, side, line)."""
    positions = {}
    old_path = None
    new_path = None
    old_line = None
    new_line = None
    in_hunk = False

    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            old_path = None
            new_path = None
            old_line = None
            new_line = None
            in_hunk = False
            continue

        if not in_hunk and raw_line.startswith("--- "):
            old_path = clean_diff_path(raw_line[4:])
            continue

        if not in_hunk and raw_line.startswith("+++ "):
            new_path = clean_diff_path(raw_line[4:])
            continue

        match = hunk_pattern.match(raw_line)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            in_hunk = True
            continue

        if not in_hunk or old_line is None or new_line is None:
            continue

        display_path = new_path if new_path != "/dev/null" else old_path
        if not display_path or not old_path or not new_path:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            positions[(display_path, "new", new_line)] = {
                "old_path": old_path if old_path != "/dev/null" else display_path,
                "new_path": new_path if new_path != "/dev/null" else display_path,
                "new_line": new_line,
            }
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            positions[(display_path, "old", old_line)] = {
                "old_path": old_path if old_path != "/dev/null" else display_path,
                "new_path": new_path if new_path != "/dev/null" else display_path,
                "old_line": old_line,
            }
            old_line += 1
        elif raw_line.startswith(" "):
            old_line += 1
            new_line += 1
        elif raw_line.startswith("\\"):
            continue
        else:
            in_hunk = False

    return positions


def gitlab_api(base_url, token, method, path, form=None):
    url = base_url.rstrip("/") + "/api/v4" + path
    data = None
    headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"GitLab API {method} {path} returned HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot connect to GitLab API: {exc.reason}") from exc


def gitlab_base_url(repository_url):
    configured = os.environ.get("GITLAB_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    parsed = urllib.parse.urlparse(repository_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "Cannot derive GitLab URL from repository URL; set GITLAB_BASE_URL."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def latest_mr_version(base_url, token, project_id, mr_iid):
    project = urllib.parse.quote(str(project_id), safe="")
    versions = gitlab_api(
        base_url,
        token,
        "GET",
        f"/projects/{project}/merge_requests/{mr_iid}/versions",
    )
    if not isinstance(versions, list) or not versions:
        raise RuntimeError("GitLab returned no merge request diff versions.")
    latest = versions[0]
    return {
        "base_sha": latest["base_commit_sha"],
        "start_sha": latest["start_commit_sha"],
        "head_sha": latest["head_commit_sha"],
    }


def inline_body(comment):
    return (
        f"**风险等级：{comment['severity'].capitalize()}**\n\n"
        f"**问题：{comment['title']}**\n\n"
        f"{comment['problem']}\n\n"
        f"**风险：** {comment['risk']}\n\n"
        f"**建议：** {comment['suggestion']}"
    )


def post_inline_comment(
    base_url, token, project_id, mr_iid, version, position, comment
):
    project = urllib.parse.quote(str(project_id), safe="")
    form = {
        "body": inline_body(comment),
        "position[position_type]": "text",
        "position[base_sha]": version["base_sha"],
        "position[start_sha]": version["start_sha"],
        "position[head_sha]": version["head_sha"],
        "position[old_path]": position["old_path"],
        "position[new_path]": position["new_path"],
    }
    if comment["side"] == "new":
        form["position[new_line]"] = position["new_line"]
    else:
        form["position[old_line]"] = position["old_line"]

    return gitlab_api(
        base_url,
        token,
        "POST",
        f"/projects/{project}/merge_requests/{mr_iid}/discussions",
        form,
    )


def markdown_escape_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def fallback_section(review, fallback_items):
    if not fallback_items:
        return ""

    lines = [
        "---",
        "",
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

    lines = [
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
    if fallback:
        lines.extend(["", fallback])
    return "\n".join(lines).rstrip() + "\n"


def post_summary_note(base_url, token, project_id, mr_iid, body):
    project = urllib.parse.quote(str(project_id), safe="")
    return gitlab_api(
        base_url,
        token,
        "POST",
        f"/projects/{project}/merge_requests/{mr_iid}/notes",
        {"body": body},
    )


def main():
    deepseek_api_key = required_env("DEEPSEEK_API_KEY")
    repository_url = required_env("EFFECTIVE_REPOSITORY_URL")
    source_branch = os.environ.get("EFFECTIVE_SOURCE_BRANCH", "unknown")
    target_branch = os.environ.get("EFFECTIVE_TARGET_BRANCH", "unknown")
    mr_iid = os.environ.get("EFFECTIVE_MR_IID", "").strip()
    project_id = os.environ.get("EFFECTIVE_PROJECT_ID", "").strip()
    publish = os.environ.get("PUBLISH_COMMENT", "false").lower() in {
        "1",
        "true",
        "yes",
    }

    full_diff = read_text("mr.diff")
    truncated = len(full_diff) > MAX_DIFF_CHARS
    diff_for_ai = full_diff[:MAX_DIFF_CHARS]

    system_prompt = r'''
你是资深代码审查员。仅基于“当前 MR diff”审查，禁止报告不在本次 diff 中的问题。
输出必须是单个合法 JSON 对象，不要 Markdown 代码围栏，不要解释文字。

检查范围：
1. 检查代码和配置文件，包括 .c、.h、.py、.arxml；C 代码参考 MISRA C。
2. 检查 AUTOSAR R19-11 或更高版本相关合规风险。
3. 检查 ISO 26262 功能安全风险及 ASPICE 工程质量问题。
4. 识别安全漏洞、功能错误、性能问题和可维护性问题。

输出格式：
{
  "summary": "总体结论",
  "change_intent": "对本次修改意图的概括",
  "changes": [
    {"path": "与 diff 完全一致的文件路径", "description": "该文件的变更内容"}
  ],
  "comments": [
    {
      "title": "问题标题",
      "path": "与 diff 中 new_path 完全一致的文件路径",
      "line": 1,
      "side": "new|old",
      "severity": "low|medium|high",
      "problem": "问题描述",
      "risk": "可能造成的风险",
      "suggestion": "具体修复建议"
    }
  ]
}

硬性要求：
1. 仅评论本次 diff 中以 + 或 - 标记的真实变更行。
2. 新增行使用 side=new 和新文件行号；删除行使用 side=old 和旧文件行号。
3. path 必须与 diff 路径完全一致，区分大小写，禁止改写路径。
4. 无法确认准确路径、side 或行号的问题不要放入 comments，可在 summary 中概括。
5. severity 只能是 low、medium、high。
6. comments 最多 10 条，同一 path、side、line 只能保留一条。
7. comments 按 high、medium、low 排序，同级按 path 和 line 排序。
8. 不得输出 schema 以外字段，不得输出 null、注释或尾逗号。
9. 没有问题时返回空 comments，但仍填写 summary、change_intent 和 changes。
'''.strip()

    user_prompt = f'''
请审查以下 Merge Request。
MR: !{mr_iid or "unknown"}
源分支: {source_branch}
目标分支: {target_branch}
Diff 是否已截断: {"是，仅提供前 60000 个字符" if truncated else "否"}

--- BEGIN DIFF ---
{diff_for_ai}
--- END DIFF ---
'''.strip()

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 3000,
        "thinking": {"type": "disabled"},
        "stream": False,
    }

    result = deepseek_request(payload, deepseek_api_key)
    try:
        raw_review = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek response does not contain message content.") from exc
    if not isinstance(raw_review, str):
        raise RuntimeError("DeepSeek message content is not text.")

    write_text("review-raw.txt", raw_review.strip() + "\n")

    parse_error = None
    try:
        review = normalize_review(extract_json_object(raw_review))
    except (ValueError, json.JSONDecodeError) as exc:
        parse_error = str(exc)
        review = {
            "summary": "AI 返回内容无法解析为约定的 JSON，未发布行级评论。",
            "change_intent": "无法从非结构化输出中可靠提取修改意图。",
            "changes": [],
            "comments": [
                {
                    "title": "AI 输出格式异常",
                    "path": "",
                    "line": 0,
                    "side": "new",
                    "severity": "medium",
                    "problem": "AI 没有返回符合约定结构的 JSON。",
                    "risk": "无法验证文件路径和行号，不能安全发布行级评论。",
                    "suggestion": "查看 Jenkins 归档中的 review-raw.txt，并重新执行审查。",
                }
            ],
        }

    write_json("review-result.json", review)

    changed_positions = parse_changed_lines(full_diff)
    fallback_items = []
    posted_count = 0

    if parse_error:
        fallback_items.append(
            {"comment": review["comments"][0], "reason": parse_error}
        )

    if publish and not parse_error:
        gitlab_token = required_env("GITLAB_API_TOKEN")
        if not project_id or not mr_iid:
            raise RuntimeError("Project ID and MR IID are required for publishing.")

        base_url = gitlab_base_url(repository_url)
        version = latest_mr_version(
            base_url, gitlab_token, project_id, mr_iid
        )

        for comment in review["comments"]:
            position = changed_positions.get(
                (comment["path"], comment["side"], comment["line"])
            )
            if position is None:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": "path、side、line 未匹配到本次 MR 的真实变更行。",
                    }
                )
                continue

            if not comment["problem"] or not comment["risk"] or not comment["suggestion"]:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": "问题、风险或建议字段不完整。",
                    }
                )
                continue

            try:
                post_inline_comment(
                    base_url,
                    gitlab_token,
                    project_id,
                    mr_iid,
                    version,
                    position,
                    comment,
                )
                posted_count += 1
            except RuntimeError as exc:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": f"GitLab 行级 Discussion 发布失败：{exc}",
                    }
                )

    elif not publish and not parse_error:
        for comment in review["comments"]:
            position = changed_positions.get(
                (comment["path"], comment["side"], comment["line"])
            )
            if position is None:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": "path、side、line 未匹配到本次 MR 的真实变更行。",
                    }
                )

    summary_note = build_summary_note(
        review,
        fallback_items,
        posted_count,
        os.environ.get("BUILD_NUMBER", "unknown"),
    )
    write_text("review-result.md", summary_note)

    if publish:
        gitlab_token = required_env("GITLAB_API_TOKEN")
        base_url = gitlab_base_url(repository_url)
        post_summary_note(
            base_url, gitlab_token, project_id, mr_iid, summary_note
        )

    print(
        "Review finished: "
        f"comments={len(review['comments'])}, "
        f"inline_posted={posted_count}, "
        f"fallback={len(fallback_items)}, "
        f"summary_posted={publish}"
    )


if __name__ == "__main__":
    main()
