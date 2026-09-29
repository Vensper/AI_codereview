import os
import urllib.parse

from utils.common import (
    post_failure_note,
    publishing_context,
    publishing_enabled,
    update_mr_labels,
)


MAX_FAILURE_STAGE_LENGTH = 40


def normalize_failure_stage(value):
    """Keep a short, single-line Jenkins-controlled stage name."""
    stage = str(value or "").strip()
    if (
        not stage
        or len(stage) > MAX_FAILURE_STAGE_LENGTH
        or "\r" in stage
        or "\n" in stage
    ):
        return "未知阶段"
    return stage


def main():
    if not publishing_enabled():
        print("Publishing disabled; failure notification skipped.")
        return

    base_url, token, project_id, mr_iid = publishing_context()
    stage = normalize_failure_stage(os.environ.get("REVIEW_FAILURE_STAGE"))
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
        post_failure_note(
            base_url, token, project_id, mr_iid, body,
            os.environ.get("BUILD_TAG", "").strip(),
        )
    except RuntimeError as exc:
        failures.append(f"failure note: {exc}")
    if failures:
        raise RuntimeError("; ".join(failures))
    print("Failure label and sanitized failure note published.")


if __name__ == "__main__":
    main()
