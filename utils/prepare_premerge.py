# -*- coding: utf-8 -*-
"""本地预合并工作区准备模块。

在 Jenkins 构建期间，以 MR 目标分支为基线，把已固定的 MR 源 commit SHA
合并到独立的 Git worktree 中，生成本次构建专用的预合并结果。

设计约束：
- 绝不创建或推送 GitLab 远端分支，不修改 MR 源分支；
- 原始源码工作树（mr-source/）只读，绝不在其中执行 merge；
- 预合并结果只写入本地独立 worktree（mr-premerge/）；
- 本模块不读取、不打印任何 GitLab token、DeepSeek key 等凭据；
- 所有 Git 命令均使用参数数组调用，不拼接 shell 命令，避免注入。

命令行用法：
    python -m utils.prepare_premerge <mr-source 目录> <mr-premerge 目录>

环境变量：
- EFFECTIVE_SOURCE_SHA：MR 已固定的源 commit SHA；
- EFFECTIVE_TARGET_BRANCH：MR 目标分支名。

结果文件 premerge-result.json 写入当前目录（review-tool/）：
- status 为 "merged"：预合并成功，包含 premerge_sha；
- status 为 "conflict"：合并冲突，包含 conflict_files；
- status 为 "failed"：其他 Git 失败，包含错误说明。

冲突与其他失败均以非零退出码结束，让 Jenkins 停在当前 stage。
"""

import os
import re
import subprocess
import sys

from utils.common import required_env, write_json

RESULT_FILE = "premerge-result.json"

# 合并结果 commit 使用的固定临时身份，仅写入本地合并 commit，不推送。
GIT_USER_NAME = "jenkins-ai-review"
GIT_USER_EMAIL = "jenkins-ai-review@invalid"

FULL_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class PremergeError(RuntimeError):
    """预合并准备过程中的可预期失败，信息可安全写入结果文件。"""


def run_git(args, cwd):
    """以参数数组形式执行 Git 命令，返回 CompletedProcess。

    不经过 shell，不输出环境变量，stdout/stderr 仅捕获为文本，
    供调用方决定是否打印其中的路径、SHA 和状态信息。
    """
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def require_git_success(completed, action, context):
    """检查 Git 命令退出码，失败时抛出 PremergeError。

    错误信息只包含动作描述、退出码和 Git 的 stderr 摘要，
    不包含任何凭据或环境变量值。
    """
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        detail = f"exit code {completed.returncode}"
        if stderr:
            detail += f": {stderr}"
        raise PremergeError(f"git {action} 失败（{context}），{detail}")


def verify_source_state(source_dir, expected_sha):
    """验证 mr-source 工作树的 HEAD 与已固定的源 SHA 完全一致。

    同时确认 expected_sha 本身是合法的 40 位 commit SHA。
    不对 mr-source 工作树做任何修改。
    """
    if not FULL_SHA_PATTERN.match(expected_sha):
        raise PremergeError(
            f"EFFECTIVE_SOURCE_SHA 不是合法的 40 位 commit SHA：{expected_sha}"
        )
    completed = run_git(["rev-parse", "HEAD"], source_dir)
    require_git_success(completed, "rev-parse HEAD", source_dir)
    head_sha = completed.stdout.strip().lower()
    if head_sha != expected_sha.lower():
        raise PremergeError(
            "mr-source HEAD 与 EFFECTIVE_SOURCE_SHA 不一致："
            f"HEAD={head_sha}，期望={expected_sha.lower()}"
        )
    return head_sha


def resolve_target_sha(source_dir, target_branch):
    """验证 origin/<目标分支> 已被获取，并返回其 commit SHA。"""
    remote_ref = f"origin/{target_branch}"
    completed = run_git(
        ["rev-parse", "--verify", f"refs/remotes/{remote_ref}"],
        source_dir,
    )
    if completed.returncode != 0:
        raise PremergeError(
            f"mr-source 仓库中找不到 {remote_ref}，请确认 Jenkins 已获取全部远端 heads。"
        )
    target_sha = completed.stdout.strip()
    if not FULL_SHA_PATTERN.match(target_sha):
        raise PremergeError(f"{remote_ref} 解析结果不是合法 commit SHA：{target_sha}")
    return target_sha.lower()


def prepare_worktree_dir(premerge_dir):
    """确保预合并 worktree 目录处于安全可创建状态。

    仅允许两种情况：
    - 目录不存在（由 git worktree add 创建）；
    - 目录存在且完全为空。

    任何非空目录或无法确认状态的目录都会直接失败，
    绝不递归删除未知内容。
    """
    if os.path.exists(premerge_dir):
        if not os.path.isdir(premerge_dir):
            raise PremergeError(f"预合并目录已被文件占用：{premerge_dir}")
        if os.listdir(premerge_dir):
            raise PremergeError(
                f"预合并目录已存在且非空，拒绝覆盖：{premerge_dir}"
            )


def create_premerge_worktree(source_dir, premerge_dir, target_sha):
    """基于 target_sha 创建独立的 detached worktree，不改动 mr-source。"""
    completed = run_git(
        ["worktree", "add", "--detach", premerge_dir, target_sha],
        source_dir,
    )
    require_git_success(completed, "worktree add", premerge_dir)


def collect_conflict_files(premerge_dir):
    """收集当前合并冲突的文件列表（未合并阶段文件）。"""
    completed = run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        premerge_dir,
    )
    require_git_success(completed, "diff --name-only --diff-filter=U", premerge_dir)
    return [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]


def run_premerge_merge(premerge_dir, source_sha):
    """在预合并 worktree 中执行 --no-ff 合并。

    通过命令行 -c 选项传入固定的临时 Jenkins 身份，
    不写入任何 Git 配置文件，也不触碰 mr-source 的配置。
    即使可以 fast-forward，也强制生成一个本地合并结果 commit。

    返回：
    - merge_result 为 True 表示合并成功；
    - merge_result 为 False 且 conflict_files 非空表示发生冲突，
      此时已执行 git merge --abort 恢复 worktree。
    """
    completed = run_git(
        [
            "-c", f"user.name={GIT_USER_NAME}",
            "-c", f"user.email={GIT_USER_EMAIL}",
            "merge", "--no-ff", "--no-edit", source_sha,
        ],
        premerge_dir,
    )
    if completed.returncode == 0:
        return True, []

    conflict_files = collect_conflict_files(premerge_dir)
    if conflict_files:
        abort = run_git(["merge", "--abort"], premerge_dir)
        if abort.returncode != 0:
            stderr = (abort.stderr or "").strip()
            print(f"警告：git merge --abort 未完全成功：{stderr}")
        return False, conflict_files

    # 非冲突的其他 Git 失败。
    stderr = (completed.stderr or "").strip()
    raise PremergeError(
        f"git merge 失败（exit code {completed.returncode}）：{stderr}"
    )


def read_head_sha(premerge_dir):
    """读取预合并 worktree 的 HEAD commit SHA。"""
    completed = run_git(["rev-parse", "HEAD"], premerge_dir)
    require_git_success(completed, "rev-parse HEAD", premerge_dir)
    return completed.stdout.strip().lower()


def write_result(payload):
    """把预合并结果写入当前目录的 premerge-result.json。"""
    write_json(RESULT_FILE, payload)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(
            "Usage: python -m utils.prepare_premerge <mr-source-dir> <mr-premerge-dir>"
        )

    source_dir = os.path.abspath(sys.argv[1])
    premerge_dir = os.path.abspath(sys.argv[2])

    source_sha = required_env("EFFECTIVE_SOURCE_SHA").strip().lower()
    target_branch = required_env("EFFECTIVE_TARGET_BRANCH").strip()

    context = {
        "target_branch": target_branch,
        "source_sha": source_sha,
    }

    try:
        if not os.path.isdir(source_dir):
            raise PremergeError(f"mr-source 目录不存在：{source_dir}")

        print(f"预合并输入：mr-source={source_dir}")
        print(f"预合并输出：mr-premerge={premerge_dir}")
        print(f"源 SHA：{source_sha}")

        verify_source_state(source_dir, source_sha)
        target_sha = resolve_target_sha(source_dir, target_branch)
        context["target_sha"] = target_sha
        print(f"目标分支 origin/{target_branch} SHA：{target_sha}")

        prepare_worktree_dir(premerge_dir)
        create_premerge_worktree(source_dir, premerge_dir, target_sha)

        merged, conflict_files = run_premerge_merge(premerge_dir, source_sha)
        if not merged:
            result = {
                "status": "conflict",
                **context,
                "conflict_files": conflict_files,
            }
            write_result(result)
            print(f"预合并发生冲突，冲突文件 {len(conflict_files)} 个：")
            for name in conflict_files:
                print(f"  - {name}")
            print("已执行 git merge --abort，本次构建将在准备预合并工作区阶段失败。")
            return 1

        premerge_sha = read_head_sha(premerge_dir)
        result = {
            "status": "merged",
            **context,
            "premerge_sha": premerge_sha,
        }
        write_result(result)
        print(f"预合并成功：premerge_sha={premerge_sha}")
        return 0

    except PremergeError as exc:
        result = {"status": "failed", **context, "error": str(exc)}
        write_result(result)
        print(f"预合并准备失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
