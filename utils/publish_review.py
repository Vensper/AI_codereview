import json
import os
import urllib.parse

from utils.common import (
    gitlab_api,
    gitlab_base_url,
    post_summary_note,
    publishing_enabled,
    read_text,
    required_env,
    review_done_marker,
    update_mr_labels,
    update_mr_note,
    write_text,
)
from utils.diff_utils import latest_mr_version, parse_changed_lines
from utils.review_result import build_summary_note, inline_body


def post_inline_comment(
    base_url, token, project_id, mr_iid, version, position, comment
):
    project = urllib.parse.quote(str(project_id), safe="")
    form = {
        "body": inline_body(comment),
        "position[position_type]": "text",
        "position[base_sha]": version["base_sha"],
        "position[start_sha]": version["start_sha"],
        "position[head_sha]": version["head_sha"],
        "position[old_path]": position["old_path"],
        "position[new_path]": position["new_path"],
    }
    if comment["side"] == "new":
        form["position[new_line]"] = position["new_line"]
    else:
        form["position[old_line]"] = position["old_line"]

    return gitlab_api(
        base_url,
        token,
        "POST",
        f"/projects/{project}/merge_requests/{mr_iid}/discussions",
        form,
    )


def main():
    repository_url = required_env("EFFECTIVE_REPOSITORY_URL")
    source_sha = required_env("EFFECTIVE_SOURCE_SHA").lower()
    mr_iid = os.environ.get("EFFECTIVE_MR_IID", "").strip()
    project_id = os.environ.get("EFFECTIVE_PROJECT_ID", "").strip()
    publish = publishing_enabled()

    full_diff = read_text("mr.diff")
    with open("review-result.json", "r", encoding="utf-8") as handle:
        review = json.load(handle)

    changed_positions = parse_changed_lines(full_diff)
    fallback_items = []
    posted_count = 0

    if publish:
        gitlab_token = required_env("GITLAB_API_TOKEN")
        if not project_id or not mr_iid:
            raise RuntimeError("Project ID and MR IID are required for publishing.")

        base_url = gitlab_base_url(repository_url)
        version = latest_mr_version(
            base_url, gitlab_token, project_id, mr_iid
        )
        if str(version["head_sha"]).lower() != source_sha:
            raise RuntimeError(
                "Merge Request changed during this build; "
                "its latest source SHA no longer matches the checked-out code."
            )

        for comment in review["comments"]:
            position = changed_positions.get(
                (comment["path"], comment["side"], comment["line"])
            )
            if position is None:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": "path、side、line 未匹配到本次 MR 的真实变更行。",
                    }
                )
                continue

            if not comment["problem"] or not comment["risk"] or not comment["suggestion"]:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": "问题、风险或建议字段不完整。",
                    }
                )
                continue

            try:
                post_inline_comment(
                    base_url,
                    gitlab_token,
                    project_id,
                    mr_iid,
                    version,
                    position,
                    comment,
                )
                posted_count += 1
            except RuntimeError as exc:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": f"GitLab 行级 Discussion 发布失败：{exc}",
                    }
                )

    else:
        for comment in review["comments"]:
            position = changed_positions.get(
                (comment["path"], comment["side"], comment["line"])
            )
            if position is None:
                fallback_items.append(
                    {
                        "comment": comment,
                        "reason": "path、side、line 未匹配到本次 MR 的真实变更行。",
                    }
                )

    summary_note = build_summary_note(
        review,
        fallback_items,
        posted_count,
        os.environ.get("BUILD_NUMBER", "unknown"),
    )
    write_text("review-result.md", summary_note)

    if publish:
        gitlab_token = required_env("GITLAB_API_TOKEN")
        base_url = gitlab_base_url(repository_url)
        summary_note_response = post_summary_note(
            base_url, gitlab_token, project_id, mr_iid, summary_note
        )
        update_mr_labels(
            base_url,
            gitlab_token,
            project_id,
            mr_iid,
            add=["ai_code_review_done"],
            remove=["ai_code_reviewing", "ai_code_review_failed"],
        )
        note_id = (
            summary_note_response.get("id")
            if isinstance(summary_note_response, dict)
            else None
        )
        if not isinstance(note_id, int):
            raise RuntimeError("GitLab did not return an ID for the summary note.")
        update_mr_note(
            base_url,
            gitlab_token,
            project_id,
            mr_iid,
            note_id,
            summary_note.rstrip() + "\n\n" + review_done_marker(source_sha) + "\n",
        )

    print(
        "Review finished: "
        f"comments={len(review['comments'])}, "
        f"inline_posted={posted_count}, "
        f"fallback={len(fallback_items)}, "
        f"summary_posted={publish}"
    )


if __name__ == "__main__":
    main()
