import json
import os
import sys
import time
import urllib.error
import urllib.request

import utils.review_agent_tools as agent_tools
from utils.ai_executors import create_cli_executor, executor_name_from_env
from utils.ai_executors.base import (
    ExecutionBudget,
    ExecutionBudgetExceeded,
    ReviewDeadline,
    ReviewDeadlineExceeded,
)
from utils.common import read_text, required_env, write_json, write_text
from utils.diff_utils import number_diff_for_ai
from utils.review_contract import (
    REVIEW_SCHEMA_PATH,
    generate_review_with_retry,
    load_review_schema as load_contract_schema,
    validate_review_text as validate_contract_review_text,
)
from utils.review_prompt import (
    SYSTEM_PROMPT,
    build_cli_prompt,
    build_investigation_prompt,
    build_user_prompt,
)


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_RESPONSES_API_URL = "https://api.deepseek.com/responses"
MODEL = "deepseek-v4-flash"
MAX_DIFF_CHARS = 60000
FINAL_JSON_MAX_TOKENS = 6000

# 默认预合并工作区路径（相对 review-tool 目录）。
DEFAULT_PREMERGE_ROOT = "../mr-premerge"


def _deepseek_request(url, payload, api_key, deadline=None, phase="investigation"):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    last_error = None
    for attempt in range(1, 4):
        if deadline is None:
            request_timeout = 180
        elif phase == "investigation":
            request_timeout = min(180, deadline.require_investigation_time())
        else:
            request_timeout = min(180, deadline.require_finalization_time())
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
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
        delay = attempt * 2
        if deadline is None:
            time.sleep(delay)
        else:
            deadline.sleep(delay, phase)
    raise last_error


def deepseek_request(payload, api_key, deadline=None):
    """Call DeepSeek Chat Completions for repository tool exploration."""
    return _deepseek_request(
        DEEPSEEK_API_URL,
        payload,
        api_key,
        deadline=deadline,
        phase="investigation",
    )


def deepseek_responses_request(payload, api_key, deadline=None):
    """Call DeepSeek Responses API for schema-constrained final output."""
    return _deepseek_request(
        DEEPSEEK_RESPONSES_API_URL,
        payload,
        api_key,
        deadline=deadline,
        phase="finalization",
    )


def load_review_schema(path=REVIEW_SCHEMA_PATH):
    """Backward-compatible export of the executor-neutral schema loader."""
    return load_contract_schema(path)


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


def run_review_loop(base_payload, deepseek_api_key, deadline=None, budget=None):
    """执行受控的 DeepSeek 工具调查循环，返回模型生成的审查草稿。

    - 调查阶段由共享截止时间控制；64 轮/128 次仅作为异常保险丝；
    - 模型返回 tool_calls 时保留完整 assistant 消息（含 reasoning_content）
      并回传，工具结果以 role=tool 消息附上同一 tool_call_id；
    - 工具额度用完后停止 Chat Completions 调查；最终 JSON 由 Responses API 生成。
    """
    messages = base_payload["messages"]
    budget = budget or ExecutionBudget()
    tool_round = 0

    while True:
        try:
            if deadline is not None:
                deadline.require_investigation_time()
            budget.consume_round()
        except (ReviewDeadlineExceeded, ExecutionBudgetExceeded):
            return ""
        try:
            if deadline is None:
                result = deepseek_request(base_payload, deepseek_api_key)
            else:
                result = deepseek_request(
                    base_payload, deepseek_api_key, deadline=deadline
                )
        except ReviewDeadlineExceeded:
            return ""
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
            messages.append(dict(message))
            return content

        tool_round += 1
        fuse_exhausted = False

        # 保留完整 assistant 消息（含 tool_calls 与可能的 reasoning_content）。
        messages.append(dict(message))

        for tool_call in tool_calls:
            try:
                tool_name = tool_call["function"]["name"]
                arguments = tool_call["function"].get("arguments", "{}")
                tool_call_id = tool_call.get("id", "")
            except (KeyError, TypeError) as exc:
                raise RuntimeError(
                    "DeepSeek 返回了无法解析的 tool_call 结构。"
                ) from exc
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise RuntimeError("DeepSeek 返回的 tool_call 缺少有效 ID。")

            try:
                budget.consume_tool_calls()
            except ExecutionBudgetExceeded:
                fuse_exhausted = True
                # 每个 assistant tool_call 都必须有对应的 tool 回复，但不再执行超额调用。
                outcome = {
                    "status": "error",
                    "error_code": "tool_budget_exhausted",
                    "message": "工具调用异常保险丝已触发，请根据已有信息完成审查。",
                }
            else:
                outcome = agent_tools.execute_tool_call(tool_name, arguments)
            agent_tools.append_tool_trace(
                tool_round, tool_name, arguments, outcome
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(outcome, ensure_ascii=False),
                }
            )
        if fuse_exhausted:
            return ""


def chat_messages_to_responses(messages):
    """Translate the Chat Completions exploration transcript to Responses input."""
    instructions = []
    input_items = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                instructions.append(content.strip())
            continue
        if role in {"user", "assistant"}:
            if isinstance(content, str) and content.strip():
                input_items.append(
                    {"type": "message", "role": role, "content": content}
                )
            if role == "assistant":
                for tool_call in message.get("tool_calls") or []:
                    function = tool_call.get("function") or {}
                    arguments = function.get("arguments", "{}")
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call.get("id", ""),
                            "name": function.get("name", ""),
                            "arguments": arguments,
                        }
                    )
            continue
        if role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id", ""),
                    "output": content if isinstance(content, str) else "",
                }
            )
    return "\n\n".join(instructions), input_items


def structured_response_payload(messages, schema, retry_items=None):
    instructions, input_items = chat_messages_to_responses(messages)
    input_items.append(
        {
            "type": "message",
            "role": "user",
            "content": (
                "工具调查已经结束。请只根据当前 MR diff 和已取得的工具上下文给出最终审核。"
                "无法确认的问题不要编造；没有问题时 comments 必须是空数组。"
            ),
        }
    )
    if retry_items:
        input_items.extend(retry_items)
    return {
        "model": MODEL,
        "instructions": instructions,
        "input": input_items,
        "reasoning": {"effort": "none"},
        "temperature": 0.2,
        "max_output_tokens": FINAL_JSON_MAX_TOKENS,
        "stream": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "code_review",
                "schema": schema,
            }
        },
    }


def response_output_text(result):
    if not isinstance(result, dict):
        raise RuntimeError("DeepSeek Responses API returned an invalid response.")
    status = result.get("status")
    if status != "completed":
        reason = (result.get("incomplete_details") or {}).get("reason") or status or "unknown"
        raise RuntimeError(f"DeepSeek structured response did not complete: {reason}.")
    parts = []
    for item in result.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    raw = "".join(parts).strip()
    if not raw:
        raise RuntimeError("DeepSeek structured response contains no output text.")
    return raw


def validate_review_text(raw_review, schema):
    """Backward-compatible export of the executor-neutral validator."""
    return validate_contract_review_text(raw_review, schema)


def parse_review_with_retry(
    payload,
    deepseek_api_key,
    raw_review,
    deadline=None,
    budget=None,
):
    """Legacy DeepSeek finalizer using the shared structured-review contract."""
    schema = load_review_schema()
    budget = budget or ExecutionBudget()

    def generate(_attempt, previous_output, validation_error):
        budget.consume_round()
        retry_items = []
        if validation_error:
            if previous_output is not None:
                retry_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": previous_output,
                    }
                )
            retry_items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": (
                        "上一次结构化审核未通过校验："
                        f"{validation_error} 请重新生成完整审核；禁止省略任何必填字段。"
                    ),
                }
            )
        response_payload = structured_response_payload(
            payload["messages"], schema, retry_items
        )
        if deadline is None:
            result = deepseek_responses_request(response_payload, deepseek_api_key)
        else:
            result = deepseek_responses_request(
                response_payload, deepseek_api_key, deadline=deadline
            )
        try:
            return response_output_text(result)
        except RuntimeError as exc:
            # Incomplete/malformed model output is retryable; transport/auth errors above are not.
            raise ValueError(str(exc)) from exc

    return generate_review_with_retry(
        generate,
        schema,
        initial_draft=raw_review,
        write_text_fn=write_text,
        check_deadline=(deadline.require_finalization_time if deadline else None),
    )


def run_cli_review(executor, premerge_root, user_prompt, schema, deadline, budget):
    """Run one CLI review and always persist diagnostics and release resources."""
    try:
        investigation = executor.investigate(
            build_investigation_prompt(SYSTEM_PROMPT, user_prompt),
            premerge_root,
            timeout_seconds=deadline.require_investigation_time(),
            budget=budget,
        )
        deadline.begin_finalization()

        def generate(attempt, previous_output, validation_error):
            prompt = build_cli_prompt(
                SYSTEM_PROMPT,
                user_prompt,
                investigation=investigation,
                previous_output=previous_output,
                error=validation_error,
            )
            return executor.finalize(
                prompt,
                premerge_root,
                schema,
                timeout_seconds=deadline.require_finalization_time(),
                budget=budget,
                attempt=attempt,
            )

        return generate_review_with_retry(
            generate,
            schema,
            write_text_fn=write_text,
            check_deadline=deadline.require_finalization_time,
        )
    finally:
        active_error = sys.exc_info()[1]
        trace_error = None
        try:
            write_json("ai-tool-trace.json", executor.get_trace())
        except Exception as exc:
            trace_error = exc
        finally:
            close = getattr(executor, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    print(
                        f"WARNING: failed to close AI executor cleanly: {exc}",
                        file=sys.stderr,
                    )
        if trace_error is not None:
            if active_error is None:
                raise trace_error
            print(
                f"WARNING: failed to persist AI executor trace: {trace_error}",
                file=sys.stderr,
            )


def main():
    premerge_root_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PREMERGE_ROOT
    premerge_root, premerge_meta = load_premerge_context(premerge_root_arg)
    required_env("EFFECTIVE_REPOSITORY_URL")
    required_env("EFFECTIVE_SOURCE_SHA").lower()
    source_branch = os.environ.get("EFFECTIVE_SOURCE_BRANCH", "unknown")
    target_branch = os.environ.get("EFFECTIVE_TARGET_BRANCH", "unknown")
    mr_iid = os.environ.get("EFFECTIVE_MR_IID", "").strip()

    full_diff = read_text("mr.diff")
    truncated = len(full_diff) > MAX_DIFF_CHARS
    raw_diff_for_ai = full_diff[:MAX_DIFF_CHARS]
    diff_for_ai = number_diff_for_ai(raw_diff_for_ai)

    user_prompt = build_user_prompt(
        mr_iid=mr_iid,
        source_branch=source_branch,
        target_branch=target_branch,
        premerge_meta=premerge_meta,
        diff_for_ai=diff_for_ai,
        truncated=truncated,
    )
    executor_name = executor_name_from_env(os.environ)
    deadline = ReviewDeadline()
    budget = ExecutionBudget()

    if executor_name != "deepseek":
        schema = load_review_schema()
        executor = create_cli_executor(executor_name)
        review = run_cli_review(
            executor,
            premerge_root,
            user_prompt,
            schema,
            deadline,
            budget,
        )
    else:
        agent_tools.set_root(premerge_root)
        agent_tools.reset_trace()
        deepseek_api_key = required_env("DEEPSEEK_API_KEY")

        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
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
            raw_review = run_review_loop(
                payload,
                deepseek_api_key,
                deadline=deadline,
                budget=budget,
            )
        finally:
            # 无论成败都落盘 trace，便于排查工具行为；trace 仅含元数据。
            write_json("ai-tool-trace.json", agent_tools.get_trace())

        deadline.begin_finalization()
        review = parse_review_with_retry(
            payload,
            deepseek_api_key,
            raw_review,
            deadline=deadline,
            budget=budget,
        )

    write_json("review-result.json", review)
    print(
        f"AI review result prepared: executor={executor_name}, "
        f"comments={len(review['comments'])}."
    )


if __name__ == "__main__":
    main()
