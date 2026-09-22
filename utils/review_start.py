import os

from utils.common import (
    has_completed_review,
    post_summary_note,
    publishing_context,
    publishing_enabled,
    required_env,
    update_mr_labels,
    write_text,
)


def bypass_completed_review_check():
    if any(
        os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}
        for name in ("FORCE_REVIEW", "REVIEW_BYPASS_DEDUP")
    ):
        return True
    return (
        os.environ.get("WEBHOOK_EVENT_KIND", "").strip().lower() == "note"
        or os.environ.get("gitlabActionType", "").strip().upper() == "NOTE"
    )


def main():
    if not publishing_enabled():
        print("Publishing disabled; start notification skipped.")
        return

    base_url, token, project_id, mr_iid = publishing_context()
    source_sha = required_env("EFFECTIVE_SOURCE_SHA")
    force_review = bypass_completed_review_check()

    if not force_review and has_completed_review(
        base_url, token, project_id, mr_iid, source_sha
    ):
        write_text("review-skip.flag", "already-completed\n")
        print(
            "Review deduplication: completed review exists for "
            f"source SHA {source_sha}; skip requested."
        )
        return

    post_summary_note(base_url, token, project_id, mr_iid, "已触发AI代码审核")
    update_mr_labels(
        base_url,
        token,
        project_id,
        mr_iid,
        add=["ai_code_reviewing"],
        remove=["ai_code_review_done", "ai_code_review_failed"],
    )
    if force_review:
        print("Review start note posted; completed-review check bypassed.")
    else:
        print("Review start note posted and reviewing label applied.")


if __name__ == "__main__":
    main()
