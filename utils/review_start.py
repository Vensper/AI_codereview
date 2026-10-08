from utils.common import (
    force_review_requested,
    has_completed_review,
    post_review_start_note,
    publishing_context,
    publishing_enabled,
    required_env,
    update_mr_labels,
    write_text,
)


def bypass_completed_review_check():
    return force_review_requested()


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
        update_mr_labels(
            base_url,
            token,
            project_id,
            mr_iid,
            add=["ai_code_review_done"],
            remove=["ai_code_reviewing", "ai_code_review_failed"],
        )
        write_text("review-skip.flag", "already-completed\n")
        print(
            "Review deduplication: completed review exists and MR labels were "
            f"reconciled for source SHA {source_sha}; skip requested."
        )
        return

    warnings = []
    try:
        post_review_start_note(
            base_url,
            token,
            project_id,
            mr_iid,
            "已触发AI代码审核",
            required_env("BUILD_TAG"),
            source_sha,
        )
    except RuntimeError as exc:
        warnings.append(f"start note: {exc}")

    try:
        update_mr_labels(
            base_url,
            token,
            project_id,
            mr_iid,
            add=["ai_code_reviewing"],
            remove=["ai_code_review_done", "ai_code_review_failed"],
        )
    except RuntimeError as exc:
        warnings.append(f"reviewing label: {exc}")

    if force_review:
        print("Review deduplication: completed-review check bypassed.")
    if warnings:
        print(
            "Review start notification incomplete; AI review will continue. "
            + "; ".join(warnings)
        )
    else:
        print("Review start note posted and reviewing label applied.")


if __name__ == "__main__":
    main()
