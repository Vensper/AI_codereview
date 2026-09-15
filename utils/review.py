import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-flash"
MAX_DIFF_CHARS = 60000

api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("DEEPSEEK_API_KEY is not available.")

with open("mr.diff", "r", encoding="utf-8", errors="replace") as f:
    full_diff = f.read()

truncated = len(full_diff) > MAX_DIFF_CHARS
diff = full_diff[:MAX_DIFF_CHARS]

mr_iid = os.environ.get("EFFECTIVE_MR_IID", "unknown")
source_branch = os.environ.get("EFFECTIVE_SOURCE_BRANCH", "unknown")
target_branch = os.environ.get("EFFECTIVE_TARGET_BRANCH", "unknown")

system_prompt = """
你是资深代码审查员。仅基于“当前 MR diff”进行审查，禁止使用仓库历史、项目外信息和主观猜测。
你的输出必须是“单个 JSON 对象”，不要 Markdown 包裹，不要任何解释性文字，不要代码块标记。
检视范围：
1）所有代码文件和配置文件，包括.c/.h/.py/.arxml后缀格式文件，基于MISRA C规范检查c代码
2)  需要检查代码AUTOSAR R19-11或更高版本合规性
3)  基于ISO 26262功能安全标准和ASPICE流程规范进行代码审查
4)  需要识别功能安全、安全漏洞、性能问题 、代码可维护性等。

## 常见检查项示例

### C/C++ 代码检查
- ✅ 指针使用前的空检查
- ✅ 数组边界检查
- ✅ 返回值检查
- ✅ 资源释放（内存、文件句柄等）
- ✅ 并发访问保护
- ✅ 魔法数字替换为常量
- ✅ 函数长度和复杂度

### AUTOSAR 规范检查
- ✅ 命名规范（函数、变量、文件）
- ✅ 模块化设计
- ✅ 接口定义清晰
- ✅ 错误处理机制
- ✅ 日志记录规范

### ASPICE 流程检查
- ✅ 需求追溯性
- ✅ 测试覆盖率
- ✅ 文档完整性
- ✅ 配置管理

输出格式（严格）：
{
  "summary": "string",
  "comments": [
    {"path":"string","line":0,"severity":"low|medium|high","body":"问题：... 风险：... 建议：..."}
  ]
}

硬性要求：
1) 仅评论本次 MR diff 中真实存在的变更文件与变更行。
2) path 必须与 diff 中的 new_path 完全一致（区分大小写，禁止路径改写）。
3) line 优先定位到该 path 在 diff 中可评论的新文件行号；若无法精确定位，仍可输出该条（保留 path/line），系统将以文件级评论回写。
4) 严禁输出任何不在 MR diff 中的文件问题。
5) severity 只能是 low / medium / high（小写）。
6) body 必须且仅能包含三段，顺序固定：
   问题：...
   风险：...
   建议：...
7) 同一 (path,line) 只保留一条最有价值评论，禁止重复。
8) comments 按风险从高到低排序（high > medium > low）；同风险按 path、line 升序。
9) 限制评论数量：最多输出 10 条最关键问题；其余不要输出。
10) 若无问题，返回：
    {"summary":"未发现需要评论的问题","comments":[]}
11) 禁止输出 schema 以外字段；禁止输出 null、注释、尾逗号；必须是可被 JSON.parse 直接解析的合法 JSON。
""".strip()

user_prompt = f"""
请审查以下 Merge Request。
MR: !{mr_iid}
源分支: {source_branch}
目标分支: {target_branch}
Diff 是否已截断: {"是，前 60000 个字符" if truncated else "否"}

--- BEGIN DIFF ---
{diff}
--- END DIFF ---
""".strip()

payload = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    "temperature": 0.2,
    "max_tokens": 3000,
    "thinking": {"type": "disabled"},
    "stream": False
}

request = urllib.request.Request(
    API_URL,
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    },
    method="POST"
)

try:
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    raise RuntimeError(f"DeepSeek API returned HTTP {e.code}.") from e
except urllib.error.URLError as e:
    raise RuntimeError(f"Cannot connect to DeepSeek API: {e.reason}") from e

review = result["choices"][0]["message"]["content"]

with open("review-result.md", "w", encoding="utf-8") as f:
    f.write("# DeepSeek MR 代码审查草稿\n\n")
    f.write(f"- MR: !{mr_iid}\n")
    f.write(f"- 分支: `{source_branch}` → `{target_branch}`\n")
    f.write(f"- Diff 截断: {'是' if truncated else '否'}\n\n")
    f.write(review.strip())
    f.write("\n")
