import os
import urllib.parse

from utils.common import (
    post_summary_note,
    publishing_context,
    publishing_enabled,
    update_mr_labels,
)


def main():
    if not publishing_enabled():
        print("Publishing disabled; failure notification skipped.")
        return

    base_url, token, project_id, mr_iid = publishing_context()
    stage = os.environ.get("REVIEW_FAILURE_STAGE", "").strip()
    if stage not in {
        "发送开始通知",
        "获取 GitLab MR diff",
        "生成本地 Git 兜底 diff",
        "AI 审查",
        "发布审查结果",
    }:
        stage = "未知阶段"
    build_url = os.environ.get("BUILD_URL", "").strip()
    parsed_url = urllib.parse.urlparse(build_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        build_url = "请查看 Jenkins 构建记录"
    body = (
        "AI代码审核失败。\n\n"
        f"- 失败阶段：{stage}\n"
        f"- Jenkins 构建：{build_url}\n"
        "- 详细错误请查看 Jenkins 控制台日志；此处不公开接口响应或凭证。"
    )

    failures = []
    try:
        update_mr_labels(
            base_url,
            token,
            project_id,
            mr_iid,
            add=["ai_code_review_failed"],
            remove=["ai_code_reviewing", "ai_code_review_done"],
        )
    except RuntimeError as exc:
        failures.append(f"label update: {exc}")
    try:
        post_summary_note(base_url, token, project_id, mr_iid, body)
    except RuntimeError as exc:
        failures.append(f"failure note: {exc}")
    if failures:
        raise RuntimeError("; ".join(failures))
    print("Failure label and sanitized failure note published.")


if __name__ == "__main__":
    main()
