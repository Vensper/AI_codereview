import json
import os
import sys
import time
import urllib.error
import urllib.request

import utils.review_agent_tools as agent_tools
from utils.common import read_text, required_env, write_json, write_text
from utils.diff_utils import number_diff_for_ai
from utils.review_result import extract_json_object, normalize_review


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
MAX_DIFF_CHARS = 60000
MAX_TOOL_ROUNDS = 8
MAX_TOOL_CALLS = 16

# 默认预合并工作区路径（相对 review-tool 目录）。
DEFAULT_PREMERGE_ROOT = "../mr-premerge"


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


def load_premerge_context(premerge_root_arg):
    """验证预合并工作区并读取结果元数据。

    只有 premerge-result.json 的 status 为 merged 时才继续。
    返回 (预合并根目录绝对路径, 元数据 dict)。
    """
    root = os.path.abspath(premerge_root_arg)
    if not os.path.isdir(root):
        raise RuntimeError(f"预合并工作区目录不存在：{root}")

    try:
        with open("premerge-result.json", "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取 premerge-result.json。") from exc
    if not isinstance(meta, dict) or meta.get("status") != "merged":
        raise RuntimeError(
            "预合并结果状态不是 merged，无法基于预合并工作区执行审查。"
        )
    return root, meta


def run_review_loop(base_payload, deepseek_api_key):
    """执行受控的 DeepSeek 工具调用循环，返回模型最终文本。

    - 最多 MAX_TOOL_ROUNDS 个工具轮次、MAX_TOOL_CALLS 次总工具调用；
    - 模型返回 tool_calls 时保留完整 assistant 消息（含 reasoning_content）
      并回传，工具结果以 role=tool 消息附上同一 tool_call_id；
    - 只有当模型不再返回 tool_calls 时，才把 content 当作最终审查 JSON；
    - 达到上限仍未得到最终回答时抛出明确错误。
    """
    messages = base_payload["messages"]
    tool_rounds = 0
    tool_calls_used = 0

    while True:
        result = deepseek_request(base_payload, deepseek_api_key)
        try:
            message = result["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "DeepSeek response does not contain message content."
            ) from exc

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError(
                    "DeepSeek 返回了既无工具调用也无正文的消息。"
                )
            return content

        tool_rounds += 1
        if (
            tool_rounds > MAX_TOOL_ROUNDS
            or tool_calls_used + len(tool_calls) > MAX_TOOL_CALLS
        ):
            raise RuntimeError(
                "AI 工具调用达到上限"
                f"（轮次 {MAX_TOOL_ROUNDS} / 调用 {MAX_TOOL_CALLS} 次），"
                "模型仍未生成最终审查 JSON。"
            )

        # 保留完整 assistant 消息（含 tool_calls 与可能的 reasoning_content）。
        messages.append(dict(message))

        for tool_call in tool_calls:
            tool_calls_used += 1
            try:
                tool_name = tool_call["function"]["name"]
                arguments = tool_call["function"].get("arguments", "{}")
                tool_call_id = tool_call.get("id", "")
            except (KeyError, TypeError) as exc:
                raise RuntimeError(
                    "DeepSeek 返回了无法解析的 tool_call 结构。"
                ) from exc

            outcome = agent_tools.execute_tool_call(tool_name, arguments)
            agent_tools.append_tool_trace(
                tool_rounds, tool_name, arguments, outcome
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(outcome, ensure_ascii=False),
                }
            )


def main():
    premerge_root_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PREMERGE_ROOT
    premerge_root, premerge_meta = load_premerge_context(premerge_root_arg)
    agent_tools.set_root(premerge_root)
    agent_tools.reset_trace()

    deepseek_api_key = required_env("DEEPSEEK_API_KEY")
    required_env("EFFECTIVE_REPOSITORY_URL")
    required_env("EFFECTIVE_SOURCE_SHA").lower()
    source_branch = os.environ.get("EFFECTIVE_SOURCE_BRANCH", "unknown")
    target_branch = os.environ.get("EFFECTIVE_TARGET_BRANCH", "unknown")
    mr_iid = os.environ.get("EFFECTIVE_MR_IID", "").strip()

    full_diff = read_text("mr.diff")
    truncated = len(full_diff) > MAX_DIFF_CHARS
    raw_diff_for_ai = full_diff[:MAX_DIFF_CHARS]
    diff_for_ai = number_diff_for_ai(raw_diff_for_ai)

    system_prompt = r'''
你是资深代码审查员。仅基于“当前 MR diff”审查，禁止报告不在本次 diff 中的问题。
输出必须是单个合法 JSON 对象，不要 Markdown 代码围栏，不要解释文字。

检查范围：
1. 检查代码和配置文件，包括 .c、.h、.py、.arxml；C 代码参考 MISRA C。
2. 检查 AUTOSAR R19-11 或更高版本相关合规风险。
3. 检查 ISO 26262 功能安全风险及 ASPICE 工程质量问题。
4. 识别安全漏洞、功能错误、性能问题和可维护性问题。

预合并源码工具（可选）：
在给出最终结论之前，你可以调用 search_repository 和 read_repository_file 工具，
检索并读取预合并工作区的完整源码，以理解工程上下文（例如被调用函数的定义、
调用方、配置的完整结构等）。
- 工具返回的源码仅用于理解上下文，不能作为评论位置依据。
- 评论位置必须且只能来自下方编号 diff 中的定位标记。
- 不得对仅出现在工具返回上下文、未出现在本次 diff 变更行（kind=added/kind=deleted）
  中的代码提出 comments。
- 不得请求工具执行命令、修改文件、读取凭据、读取 .git 或访问预合并工作区之外的
  路径；此类请求会被直接拒绝，拒绝信息会在工具结果中说明。
- 工具调查完成后，最终回答必须是下方 schema 的单个合法 JSON 对象。

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
1. 每行前的定位标记由系统生成，格式为 [old=旧行号,new=新行号,kind=类型]。
2. 仅评论 kind=added 或 kind=deleted 的真实变更行，禁止评论 kind=context 行。
3. kind=added 必须使用 side=new，并直接复制标记中的 new 行号。
4. kind=deleted 必须使用 side=old，并直接复制标记中的 old 行号。
5. 禁止自行累计、推算或修正行号；必须逐字采用定位标记中的行号。
6. path 必须与 diff 路径完全一致，区分大小写，禁止改写路径。
7. 无法确认准确路径、side 或行号的问题不要放入 comments，可在 summary 中概括。
8. severity 只能是 low、medium、high。
9. comments 最多 10 条，同一 path、side、line 只能保留一条。
10. comments 按 high、medium、low 排序，同级按 path 和 line 排序。
11. 不得输出 schema 以外字段，不得输出 null、注释或尾逗号。
12. 没有问题时返回空 comments，但仍填写 summary、change_intent 和 changes。
13. 工具调用次数有限（最多 8 轮、16 次调用），请优先检索与 diff 变更直接相关的
    内容，避免无目的浏览。
14. 最终必须输出上述 schema 的单个合法 JSON 对象，不能有 Markdown 围栏或解释文字。
'''.strip()

    user_prompt = f'''
请审查以下 Merge Request。
MR: !{mr_iid or "unknown"}
源分支: {source_branch}
目标分支: {target_branch}
源 commit SHA: {premerge_meta.get("source_sha", "unknown")}
目标分支 commit SHA: {premerge_meta.get("target_sha", "unknown")}
预合并结果 commit SHA: {premerge_meta.get("premerge_sha", "unknown")}
Diff 是否已截断: {"是，仅提供前 60000 个字符" if truncated else "否"}

--- BEGIN NUMBERED DIFF ---
{diff_for_ai}
--- END NUMBERED DIFF ---
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
        "tools": agent_tools.TOOLS_SCHEMA,
        "tool_choice": "auto",
    }

    try:
        raw_review = run_review_loop(payload, deepseek_api_key)
    finally:
        # 无论成败都落盘 trace，便于排查工具行为；trace 仅含元数据。
        write_json("ai-tool-trace.json", agent_tools.get_trace())

    write_text("review-raw.txt", raw_review.strip() + "\n")
    try:
        review = normalize_review(extract_json_object(raw_review))
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "AI output cannot be used as review JSON; see review-raw.txt."
        ) from exc

    write_json("review-result.json", review)
    print(f"AI review result prepared: comments={len(review['comments'])}.")


if __name__ == "__main__":
    main()
