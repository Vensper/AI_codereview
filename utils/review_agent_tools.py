# -*- coding: utf-8 -*-
"""AI 审查工具模块：DeepSeek 在审查时按需读取预合并工作区源码。

本模块是 AI 工具的唯一实现位置，只提供两个只读工具：
- search_repository：按普通文本子串（非正则）搜索预合并工作区内的 UTF-8 文本文件；
- read_repository_file：读取预合并工作区内一个文本文件的指定行范围。

安全边界（必须始终满足）：
- AI 没有任何文件系统写权限、Shell、Git、网络、环境变量访问能力；
- 所有路径先经 pathlib.Path.resolve()，再验证仍位于预合并工作区根目录内；
- 拒绝绝对路径、.. 路径穿越、指向工作区外的符号链接、.git 目录；
- 拒绝二进制文件（含 NUL 字节）、非 UTF-8 文件、超过 256 KiB 的文件；
- 拒绝敏感文件（.env、*.pem、*.key、*.pfx、*.p12、id_rsa 等，
  以及文件名包含 secret/credential/token/password 的文件）；
- 仅允许常见源码/配置/文档扩展名，Jenkinsfile 作为例外允许；
- 任何参数无效、越权、文件不存在或读取失败都返回结构化 JSON 错误，
  绝不让 Python 直接崩溃；
- 本模块禁止调用 subprocess / 命令行；
- trace 只记录元数据（轮次、工具名、参数摘要、状态、返回规模），
  绝不记录工具读取到的源码内容；
- 工具返回给模型的内容也不打印到 Jenkins 控制台日志。
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量：限制与允许/拒绝清单
# ---------------------------------------------------------------------------

# 单文件大小上限（字节）。
MAX_FILE_BYTES = 256 * 1024

# read_repository_file 单次读取上限。
MAX_READ_LINES = 240
MAX_READ_CHARS = 12000

# search_repository 输出上限。
MIN_QUERY_CHARS = 3
MAX_QUERY_CHARS = 80
MIN_MAX_RESULTS = 1
MAX_MAX_RESULTS = 20
DEFAULT_MAX_RESULTS = 10
MAX_SNIPPET_CHARS = 160
MAX_SEARCH_TOTAL_CHARS = 6000
MAX_MATCHES_PER_FILE = 5
MAX_SEARCH_FILES = 2000
MAX_SCAN_BYTES = 32 * 1024 * 1024

# 允许的源码/配置/文档扩展名（小写）。
ALLOWED_SUFFIXES = {
    ".c", ".h", ".py", ".arxml", ".jinja",
    ".json", ".xml", ".yml", ".yaml", ".md", ".txt",
}

# 允许的无扩展名文件名（小写）。
ALLOWED_BASENAMES = {"jenkinsfile"}

# 敏感文件名（小写、无扩展名场景）。
SENSITIVE_BASENAMES = {
    ".env",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}

# 敏感扩展名（小写）。
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12"}

# 文件名或目录名包含以下标记（小写子串）即视为敏感。
SENSITIVE_NAME_MARKERS = ("secret", "credential", "token", "password")

# 预合并工作区根目录；由 ai_review 验证后设置。
_ROOT = None

# 工具调用 trace（仅元数据）。
_TRACE = []

# 视为“安全拒绝”的错误码；其余错误码视为内部失败。
_REJECTION_CODES = {
    "root_not_initialized",
    "invalid_argument",
    "invalid_arguments_json",
    "absolute_path_rejected",
    "path_traversal_rejected",
    "git_directory_rejected",
    "outside_root_rejected",
    "sensitive_file_rejected",
    "extension_not_allowed",
    "file_too_large",
    "binary_file_rejected",
    "not_utf8_text",
    "not_found",
    "query_length_invalid",
    "range_too_large",
    "path_prefix_invalid",
    "unknown_tool",
}


# ---------------------------------------------------------------------------
# 基础辅助
# ---------------------------------------------------------------------------


def _error(error_code, message):
    """构造返回给模型的结构化 JSON 错误对象。"""
    return {"status": "error", "error_code": error_code, "message": message}


def set_root(root_path):
    """设置预合并工作区根目录。

    由 ai_review 在验证预合并结果为 merged 之后调用；
    工具函数在根目录未设置时一律返回结构化错误。
    """
    global _ROOT
    resolved = Path(root_path).resolve()
    if not resolved.is_dir():
        raise RuntimeError(f"预合并工作区根目录不存在：{resolved}")
    _ROOT = resolved


def _get_root():
    """返回已设置的根目录，未设置时抛出 RuntimeError。"""
    if _ROOT is None:
        raise RuntimeError("工具根目录尚未初始化。")
    return _ROOT


def _is_sensitive_name(name):
    """判断文件或目录名是否命中敏感标记。"""
    lower = name.lower()
    if lower in SENSITIVE_BASENAMES:
        return True
    if any(marker in lower for marker in SENSITIVE_NAME_MARKERS):
        return True
    return False


def _resolve_inside_root(relative):
    """把相对路径解析为根目录内的绝对路径。

    返回 (Path, None) 或 (None, 结构化错误)。拒绝绝对路径、
    .. 穿越、.git 目录，以及解析后（含符号链接）仍超出根目录的路径。
    """
    if not isinstance(relative, str) or not relative.strip():
        return None, _error("invalid_argument", "参数必须是相对路径字符串。")
    cleaned = relative.strip().replace("\\", "/")
    if not cleaned:
        return None, _error("invalid_argument", "参数不能为空。")
    if cleaned.startswith("/") or cleaned.startswith("~") or ":" in cleaned:
        return None, _error(
            "absolute_path_rejected", "不允许绝对路径，请使用相对预合并工作区的路径。"
        )
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts:
        return None, _error("invalid_argument", "参数不能为空。")
    if any(part == ".." for part in parts):
        return None, _error("path_traversal_rejected", "不允许使用 .. 的路径穿越。")
    if any(part == ".git" for part in parts):
        return None, _error("git_directory_rejected", "不允许访问 .git 目录。")

    root = _get_root()
    candidate = (root.joinpath(*parts)).resolve()
    if candidate != root and root not in candidate.parents:
        return None, _error(
            "outside_root_rejected", "路径超出预合并工作区范围。"
        )
    return candidate, None


def _check_readable_file(abs_path):
    """对单个文件执行全部安全检查并读取文本。

    返回 (text, None) 或 (None, 结构化错误)。
    """
    if _is_sensitive_name(abs_path.name):
        return None, _error(
            "sensitive_file_rejected",
            f"文件 {abs_path.name} 是敏感文件，禁止读取。",
        )
    lower_name = abs_path.name.lower()
    suffix = abs_path.suffix.lower()
    if suffix in SENSITIVE_SUFFIXES:
        return None, _error(
            "sensitive_file_rejected",
            f"文件 {abs_path.name} 的扩展名属于敏感凭据类型，禁止读取。",
        )
    if lower_name not in ALLOWED_BASENAMES and suffix not in ALLOWED_SUFFIXES:
        return None, _error(
            "extension_not_allowed",
            f"文件 {abs_path.name} 的扩展名不在允许的源码/配置/文档清单内。",
        )
    if not abs_path.is_file():
        return None, _error("not_found", f"文件不存在：{abs_path.name}")
    try:
        if abs_path.stat().st_size > MAX_FILE_BYTES:
            return None, _error(
                "file_too_large",
                f"文件 {abs_path.name} 超过 256 KiB，禁止读取。",
            )
        data = abs_path.read_bytes()
    except OSError:
        return None, _error("not_found", f"文件无法读取：{abs_path.name}")
    if b"\x00" in data:
        return None, _error(
            "binary_file_rejected", f"文件 {abs_path.name} 是二进制文件。"
        )
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, _error(
            "not_utf8_text", f"文件 {abs_path.name} 不是 UTF-8 文本。"
        )


def _relative_to_root(abs_path):
    """返回相对根目录的、使用正斜杠的路径。"""
    root = _get_root()
    return abs_path.resolve().relative_to(root).as_posix()


# ---------------------------------------------------------------------------
# 工具 A：search_repository
# ---------------------------------------------------------------------------


def search_repository(query, path_prefix=None, max_results=None):
    """在预合并工作区内按普通文本子串搜索 UTF-8 文本文件。

    - query：3 到 80 个字符，按字面子串（非正则、大小写不敏感）匹配；
    - path_prefix：可选的相对目录前缀，用于缩小搜索范围；
    - max_results：1 到 20，默认 10；
    - 返回匹配文件的相对路径、行号和短文本片段；
    - 每个片段不超过 160 字符，返回总字符数不超过 6000；
    - 最多扫描 2000 个文件、32 MiB 内容，超出即截断并标记。

    任何违规参数或越权访问都返回结构化 JSON 错误，不抛异常。
    """
    root = _get_root()

    if not isinstance(query, str):
        return _error("invalid_argument", "query 必须是字符串。")
    needle = query.strip()
    if not (MIN_QUERY_CHARS <= len(needle) <= MAX_QUERY_CHARS):
        return _error(
            "query_length_invalid",
            f"query 长度必须是 {MIN_QUERY_CHARS} 到 {MAX_QUERY_CHARS} 个字符。",
        )

    if max_results is None:
        limit = DEFAULT_MAX_RESULTS
    else:
        try:
            limit = int(max_results)
        except (TypeError, ValueError):
            return _error("invalid_argument", "max_results 必须是整数。")
        limit = max(MIN_MAX_RESULTS, min(MAX_MAX_RESULTS, limit))

    walk_root = root
    if path_prefix is not None:
        if not isinstance(path_prefix, str) or not path_prefix.strip():
            return _error("invalid_argument", "path_prefix 必须是非空字符串。")
        resolved_prefix, prefix_error = _resolve_inside_root(path_prefix)
        if prefix_error is not None:
            return prefix_error
        if not resolved_prefix.is_dir():
            return _error(
                "path_prefix_invalid",
                f"目录不存在：{path_prefix.strip()}",
            )
        walk_root = resolved_prefix

    needle_lower = needle.lower()
    matches = []
    total_chars = 0
    files_scanned = 0
    bytes_scanned = 0
    truncated = False

    for dirpath, dirnames, filenames in os.walk(walk_root, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if name != ".git"
            and not _is_sensitive_name(name)
            and not os.path.islink(os.path.join(dirpath, name))
        ]
        for filename in filenames:
            if len(matches) >= limit or total_chars >= MAX_SEARCH_TOTAL_CHARS:
                truncated = True
                break
            if files_scanned >= MAX_SEARCH_FILES or bytes_scanned >= MAX_SCAN_BYTES:
                truncated = True
                break
            if _is_sensitive_name(filename):
                continue
            candidate = Path(dirpath) / filename
            if candidate.name.lower() not in ALLOWED_BASENAMES and (
                candidate.suffix.lower() not in ALLOWED_SUFFIXES
            ):
                continue
            try:
                if candidate.is_symlink():
                    resolved = candidate.resolve()
                    if resolved != root and root not in resolved.parents:
                        continue
                if candidate.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = candidate.read_bytes()
            except OSError:
                continue
            if b"\x00" in data:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            files_scanned += 1
            bytes_scanned += len(data)

            file_matches = 0
            try:
                rel_path = _relative_to_root(candidate)
            except ValueError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if needle_lower not in line.lower():
                    continue
                snippet = line.strip()
                if len(snippet) > MAX_SNIPPET_CHARS:
                    snippet = snippet[:MAX_SNIPPET_CHARS]
                matches.append(
                    {"path": rel_path, "line": line_number, "snippet": snippet}
                )
                total_chars += len(snippet)
                file_matches += 1
                if (
                    file_matches >= MAX_MATCHES_PER_FILE
                    or len(matches) >= limit
                    or total_chars >= MAX_SEARCH_TOTAL_CHARS
                ):
                    if len(matches) >= limit or total_chars >= MAX_SEARCH_TOTAL_CHARS:
                        truncated = True
                    break
            if len(matches) >= limit or total_chars >= MAX_SEARCH_TOTAL_CHARS:
                truncated = True
                break

    return {
        "status": "ok",
        "query": needle,
        "path_prefix": path_prefix.strip() if isinstance(path_prefix, str) else None,
        "match_count": len(matches),
        "matches": matches,
        "files_scanned": files_scanned,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# 工具 B：read_repository_file
# ---------------------------------------------------------------------------


def read_repository_file(path, start_line=None, end_line=None):
    """读取预合并工作区内一个文本文件的指定行范围。

    - path：相对根目录的文件路径；
    - start_line：起始行号（含），从 1 开始，默认 1；
    - end_line：结束行号（含），可选；缺省时为 start_line + 239；
    - 单次最多 240 行、12000 字符，超出字符上限时截断并标记；
    - 禁止自动读取完整超大文件，必须明确指定范围。

    任何违规参数、越权路径或读取失败都返回结构化 JSON 错误，不抛异常。
    """
    resolved, path_error = _resolve_inside_root(path)
    if path_error is not None:
        return path_error

    if start_line is None:
        start = 1
    else:
        try:
            start = int(start_line)
        except (TypeError, ValueError):
            return _error("invalid_argument", "start_line 必须是整数。")
        if start < 1:
            return _error("invalid_argument", "start_line 必须从 1 开始。")

    if end_line is None:
        end = start + MAX_READ_LINES - 1
    else:
        try:
            end = int(end_line)
        except (TypeError, ValueError):
            return _error("invalid_argument", "end_line 必须是整数。")
        if end < start:
            return _error("invalid_argument", "end_line 不能小于 start_line。")

    if end - start + 1 > MAX_READ_LINES:
        return _error(
            "range_too_large",
            f"单次最多读取 {MAX_READ_LINES} 行，请缩小行范围。",
        )

    text, read_error = _check_readable_file(resolved)
    if read_error is not None:
        return read_error

    lines = text.splitlines()
    total_lines = len(lines)
    if start > total_lines:
        return _error(
            "invalid_argument",
            f"start_line={start} 超出文件总行数 {total_lines}。",
        )
    end = min(end, total_lines)

    selected = []
    used_chars = 0
    truncated = False
    for line_number in range(start, end + 1):
        line_text = lines[line_number - 1]
        if used_chars + len(line_text) > MAX_READ_CHARS:
            truncated = True
            break
        selected.append({"line": line_number, "text": line_text})
        used_chars += len(line_text)

    return {
        "status": "ok",
        "path": _relative_to_root(resolved),
        "start_line": start,
        "end_line": selected[-1]["line"] if selected else start,
        "total_lines": total_lines,
        "line_count": len(selected),
        "character_count": used_chars,
        "truncated": truncated,
        "lines": selected,
    }


# ---------------------------------------------------------------------------
# DeepSeek tools JSON schema
# ---------------------------------------------------------------------------

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_repository",
            "description": (
                "在预合并工作区内按普通文本子串（大小写不敏感，不支持正则）"
                "搜索源码文件，返回匹配文件的相对路径、行号和短片段。"
                "仅用于理解工程上下文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键字，3 到 80 个字符的普通文本。",
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "可选的相对目录前缀，用于缩小搜索范围。",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "可选的最大结果数，1 到 20，默认 10。",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_repository_file",
            "description": (
                "读取预合并工作区内一个文本文件的指定行范围（带行号返回）。"
                "单次最多 240 行、12000 字符。仅用于理解工程上下文。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对预合并工作区根目录的文件路径。",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（含），从 1 开始，默认 1。",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（含），可选；与 start_line 跨度最多 240 行。",
                    },
                },
                "required": ["path"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 统一工具执行入口
# ---------------------------------------------------------------------------


def execute_tool_call(tool_name, arguments):
    """统一执行一次模型工具调用，返回结构化 JSON 结果。

    - arguments 可以是 dict，也可以是 JSON 字符串（模型常见输出）；
    - 参数无效、越权、文件不存在或读取失败时返回结构化错误；
    - 未知工具名同样返回结构化错误；
    - 本函数捕获一切异常，绝不让 Python 直接崩溃。
    """
    try:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return _error(
                    "invalid_arguments_json", "工具参数不是合法的 JSON 对象。"
                )
        if not isinstance(arguments, dict):
            return _error("invalid_argument", "工具参数必须是 JSON 对象。")

        if tool_name == "search_repository":
            return search_repository(
                arguments.get("query"),
                arguments.get("path_prefix"),
                arguments.get("max_results"),
            )
        if tool_name == "read_repository_file":
            return read_repository_file(
                arguments.get("path"),
                arguments.get("start_line"),
                arguments.get("end_line"),
            )
        return _error(
            "unknown_tool",
            f"未知工具：{tool_name}；只允许 search_repository 和 read_repository_file。",
        )
    except RuntimeError as exc:
        return _error("root_not_initialized", str(exc))
    except Exception:  # noqa: BLE001 - 工具绝不能让审查进程崩溃
        return _error("internal_error", "工具执行出现内部错误，已安全终止该次调用。")


# ---------------------------------------------------------------------------
# trace：只记录元数据
# ---------------------------------------------------------------------------


def reset_trace():
    """清空工具调用 trace（每次审查开始时调用）。"""
    _TRACE.clear()


def _trace_status(result):
    """把工具结果映射为 trace 状态：success / rejected / failed。"""
    if isinstance(result, dict) and result.get("status") == "ok":
        return "success"
    code = result.get("error_code", "") if isinstance(result, dict) else ""
    if code and code != "internal_error":
        return "rejected"
    return "failed"


def append_tool_trace(round_number, tool_name, arguments, result):
    """向 trace 追加一条仅含元数据的记录。

    记录内容：轮次编号、工具名、经校验后的参数摘要、状态、
    返回的文件路径、行范围、匹配数、字符数等元数据。
    绝不记录工具读取到的源码内容、token 或模型思考内容。
    """
    entry = {
        "round": round_number,
        "tool": tool_name,
        "status": _trace_status(result),
    }

    summary = {}
    if isinstance(arguments, dict):
        if tool_name == "search_repository":
            query = arguments.get("query")
            if isinstance(query, str):
                summary["query"] = query
            prefix = arguments.get("path_prefix")
            if isinstance(prefix, str):
                summary["path_prefix"] = prefix
            if arguments.get("max_results") is not None:
                summary["max_results"] = arguments.get("max_results")
        else:
            path = arguments.get("path")
            if isinstance(path, str):
                summary["path"] = path
            if arguments.get("start_line") is not None:
                summary["start_line"] = arguments.get("start_line")
            if arguments.get("end_line") is not None:
                summary["end_line"] = arguments.get("end_line")
    entry["arguments"] = summary

    meta = {}
    if isinstance(result, dict):
        if result.get("status") == "ok":
            if tool_name == "search_repository":
                meta = {
                    "match_count": result.get("match_count", 0),
                    "files_scanned": result.get("files_scanned", 0),
                    "truncated": bool(result.get("truncated")),
                }
            else:
                meta = {
                    "path": result.get("path"),
                    "start_line": result.get("start_line"),
                    "end_line": result.get("end_line"),
                    "line_count": result.get("line_count", 0),
                    "character_count": result.get("character_count", 0),
                    "truncated": bool(result.get("truncated")),
                }
        else:
            meta = {"error_code": result.get("error_code", "unknown")}
    entry["result"] = meta

    _TRACE.append(entry)


def get_trace():
    """返回当前 trace 的副本（仅元数据）。"""
    return list(_TRACE)
