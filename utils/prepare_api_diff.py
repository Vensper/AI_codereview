import os

from utils.common import gitlab_base_url, required_env, write_text
from utils.diff_utils import (
    ApiDiffFallbackRequired,
    as_bool,
    fetch_gitlab_api_diff,
    write_diff_metadata,
)


def main():
    """Exit 0 for API diff, 10 only when Jenkins should run local Git fallback."""
    if as_bool(os.environ.get("FORCE_LOCAL_GIT_DIFF", "false")):
        reason = "FORCE_LOCAL_GIT_DIFF=true (manual fallback-path test)."
        write_text("diff-api-fallback-reason.txt", reason + "\n")
        print(reason)
        return 10

    repository_url = required_env("EFFECTIVE_REPOSITORY_URL")
    project_id = required_env("EFFECTIVE_PROJECT_ID")
    mr_iid = required_env("EFFECTIVE_MR_IID")
    source_sha = required_env("EFFECTIVE_SOURCE_SHA").lower()
    token = required_env("GITLAB_API_TOKEN")
    base_url = gitlab_base_url(repository_url)

    try:
        diff_text, version = fetch_gitlab_api_diff(
            base_url, token, project_id, mr_iid, source_sha
        )
    except ApiDiffFallbackRequired as exc:
        reason = str(exc)
        write_text("diff-api-fallback-reason.txt", reason + "\n")
        print(f"GitLab API diff fallback required: {reason}")
        return 10
    except RuntimeError as exc:
        message = str(exc)
        if (
            "differs from GitLab's latest" in message
            or "changed while retrieving" in message
        ):
            raise
        write_text("diff-api-fallback-reason.txt", message + "\n")
        print(f"GitLab API request failed; local fallback required: {message}")
        return 10

    write_text("mr.diff", diff_text)
    write_diff_metadata("gitlab_api", version=version)
    print(
        "MR diff prepared from GitLab API: "
        f"head_sha={version['head_sha']}, bytes={len(diff_text.encode('utf-8'))}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
