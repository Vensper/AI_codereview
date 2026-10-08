SYSTEM_PROMPT = r'''
你是资深代码审查员。仅基于“当前 MR diff”审查，禁止报告不在本次 diff 中的问题。
输出必须是单个合法 JSON 对象，不要 Markdown 代码围栏，不要解释文字。

检查范围：
1. 检查代码和配置文件，包括 .c、.h、.py、.arxml；C 代码参考 MISRA C。
2. 检查 AUTOSAR R19-11 或更高版本相关合规风险。
3. 检查 ISO 26262 功能安全风险及 ASPICE 工程质量问题。
4. 识别安全漏洞、功能错误、性能问题和可维护性问题。

预合并源码工具（可选）：
在给出最终结论之前，你可以使用执行器提供的只读搜索和文件读取工具，
检索并读取预合并工作区的完整源码，以理解工程上下文（例如被调用函数的定义、
调用方、配置的完整结构等）。
- 工具返回的源码仅用于理解上下文，不能作为评论位置依据。
- 评论位置必须且只能来自下方编号 diff 中的定位标记。
- 不得对仅出现在工具返回上下文、未出现在本次 diff 变更行（kind=added/kind=deleted）
  中的代码提出 comments。
- 不得请求工具执行命令、修改文件、读取凭据、读取 .git 或访问预合并工作区之外的
  路径；此类请求会被直接拒绝。
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
13. 请优先检索与 diff 变更直接相关的内容，避免无目的浏览。
14. 最终必须输出上述 schema 的单个合法 JSON 对象，不能有 Markdown 围栏或解释文字。
'''.strip()


def build_user_prompt(
    *,
    mr_iid,
    source_branch,
    target_branch,
    premerge_meta,
    diff_for_ai,
    truncated,
):
    return f'''
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


def build_investigation_prompt(system_prompt, user_prompt):
    return (
        f"{system_prompt}\n\n{user_prompt}\n\n"
        "这是调查阶段。请使用允许的只读工具核实与 diff 直接相关的工程上下文，"
        "并在调查结束时输出一份可供最终生成阶段使用的审查草稿。"
    )


def build_cli_prompt(
    system_prompt,
    user_prompt,
    investigation="",
    previous_output=None,
    error=None,
):
    prompt = f"{system_prompt}\n\n{user_prompt}"
    if investigation:
        prompt += f"\n\n调查阶段得到的审查草稿如下：\n{investigation}"
    prompt += (
        "\n\n这是最终生成阶段。禁止调用任何工具；请只根据当前 diff 和调查草稿，"
        "输出符合 schema 的单个 JSON 对象。"
    )
    if error:
        prompt += (
            "\n\n上一次结构化审核未通过校验："
            f"{error} 请重新生成完整审核；禁止省略任何必填字段。"
        )
        if previous_output:
            prompt += f"\n\n上一次输出如下：\n{previous_output}"
    return prompt
