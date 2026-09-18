import json
import os
import time
import urllib.error
import urllib.request

from utils.common import read_text, required_env, write_json, write_text
from utils.diff_utils import number_diff_for_ai
from utils.review_result import extract_json_object, normalize_review


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
MAX_DIFF_CHARS = 60000


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


def main():
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
'''.strip()

    user_prompt = f'''
请审查以下 Merge Request。
MR: !{mr_iid or "unknown"}
源分支: {source_branch}
目标分支: {target_branch}
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
    }

    result = deepseek_request(payload, deepseek_api_key)
    try:
        raw_review = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek response does not contain message content.") from exc
    if not isinstance(raw_review, str):
        raise RuntimeError("DeepSeek message content is not text.")

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
