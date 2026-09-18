import os
import re
import urllib.parse

from utils.common import gitlab_api, read_text, write_json, write_text


def clean_diff_path(value):
    value = value.strip()
    if value in {"/dev/null", "dev/null"}:
        return "/dev/null"
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def parse_changed_lines(diff_text):
    """Return exact changed-line positions keyed by (path, side, line)."""
    positions = {}
    old_path = None
    new_path = None
    old_line = None
    new_line = None
    in_hunk = False

    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            old_path = None
            new_path = None
            old_line = None
            new_line = None
            in_hunk = False
            continue

        if not in_hunk and raw_line.startswith("--- "):
            old_path = clean_diff_path(raw_line[4:])
            continue

        if not in_hunk and raw_line.startswith("+++ "):
            new_path = clean_diff_path(raw_line[4:])
            continue

        match = hunk_pattern.match(raw_line)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            in_hunk = True
            continue

        if not in_hunk or old_line is None or new_line is None:
            continue

        display_path = new_path if new_path != "/dev/null" else old_path
        if not display_path or not old_path or not new_path:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            positions[(display_path, "new", new_line)] = {
                "old_path": old_path if old_path != "/dev/null" else display_path,
                "new_path": new_path if new_path != "/dev/null" else display_path,
                "new_line": new_line,
            }
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            positions[(display_path, "old", old_line)] = {
                "old_path": old_path if old_path != "/dev/null" else display_path,
                "new_path": new_path if new_path != "/dev/null" else display_path,
                "old_line": old_line,
            }
            old_line += 1
        elif raw_line.startswith(" "):
            old_line += 1
            new_line += 1
        elif raw_line.startswith("\\"):
            continue
        else:
            in_hunk = False

    return positions


def number_diff_for_ai(diff_text):
    """Annotate every hunk line with exact old/new line numbers for the model."""
    output = []
    old_line = None
    new_line = None
    in_hunk = False
    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            in_hunk = False
            old_line = None
            new_line = None
            output.append(raw_line)
            continue

        match = hunk_pattern.match(raw_line)
        if match:
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            in_hunk = True
            output.append(raw_line)
            continue

        if not in_hunk or old_line is None or new_line is None:
            output.append(raw_line)
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            output.append(f"[old=-,new={new_line},kind=added] {raw_line}")
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            output.append(f"[old={old_line},new=-,kind=deleted] {raw_line}")
            old_line += 1
        elif raw_line.startswith(" "):
            output.append(
                f"[old={old_line},new={new_line},kind=context] {raw_line}"
            )
            old_line += 1
            new_line += 1
        elif raw_line.startswith("\\"):
            output.append(raw_line)
        else:
            in_hunk = False
            output.append(raw_line)

    return "\n".join(output)


class ApiDiffFallbackRequired(Exception):
    """The API diff cannot safely be used; Jenkins must run local Git fallback."""


def as_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def same_mr_version(first, second):
    return all(
        str(first[key]).lower() == str(second[key]).lower()
        for key in ("base_sha", "start_sha", "head_sha")
    )


def diff_path(path, is_dev_null):
    if is_dev_null:
        return "/dev/null"
    return str(path).lstrip("/")


def build_unified_diff_from_gitlab_changes(changes):
    """Convert GitLab /changes entries into the unified diff format we parse."""
    if not isinstance(changes, list) or not changes:
        raise ApiDiffFallbackRequired("GitLab API returned no changed files.")

    blocks = []
    for item in changes:
        if not isinstance(item, dict):
            raise ApiDiffFallbackRequired(
                "GitLab API returned an invalid changed-file entry."
            )
        if item.get("too_large") or item.get("collapsed"):
            raise ApiDiffFallbackRequired(
                "GitLab API omitted a large or collapsed file diff."
            )

        old_is_null = bool(item.get("new_file"))
        new_is_null = bool(item.get("deleted_file"))
        old_path = diff_path(item.get("old_path", ""), old_is_null)
        new_path = diff_path(item.get("new_path", ""), new_is_null)
        raw_diff = item.get("diff")

        if not old_path or not new_path or not isinstance(raw_diff, str):
            raise ApiDiffFallbackRequired(
                "GitLab API returned an incomplete file diff."
            )

        left_name = old_path if old_path != "/dev/null" else new_path
        right_name = new_path if new_path != "/dev/null" else old_path
        blocks.extend(
            [
                f"diff --git a/{left_name} b/{right_name}",
                f"--- {'/dev/null' if old_path == '/dev/null' else 'a/' + old_path}",
                f"+++ {'/dev/null' if new_path == '/dev/null' else 'b/' + new_path}",
            ]
        )
        if raw_diff:
            blocks.extend(raw_diff.rstrip("\n").splitlines())

    diff_text = "\n".join(blocks).rstrip() + "\n"
    if not diff_text.strip():
        raise ApiDiffFallbackRequired("GitLab API produced an empty unified diff.")
    return diff_text


def latest_mr_version(base_url, token, project_id, mr_iid):
    project = urllib.parse.quote(str(project_id), safe="")
    versions = gitlab_api(
        base_url,
        token,
        "GET",
        f"/projects/{project}/merge_requests/{mr_iid}/versions",
    )
    if not isinstance(versions, list) or not versions:
        raise RuntimeError("GitLab returned no merge request diff versions.")
    latest = versions[0]
    return {
        "base_sha": latest["base_commit_sha"],
        "start_sha": latest["start_commit_sha"],
        "head_sha": latest["head_commit_sha"],
    }


def fetch_gitlab_api_diff(base_url, token, project_id, mr_iid, source_sha):
    """Read the GitLab-recognized MR diff and pin it to this source revision."""
    project = urllib.parse.quote(str(project_id), safe="")
    version_before = latest_mr_version(base_url, token, project_id, mr_iid)
    if str(version_before["head_sha"]).lower() != source_sha.lower():
        raise RuntimeError(
            "The checked-out source SHA differs from GitLab's latest MR source SHA; "
            "stop instead of reviewing a stale revision."
        )

    response = gitlab_api(
        base_url,
        token,
        "GET",
        f"/projects/{project}/merge_requests/{mr_iid}/changes",
    )
    if not isinstance(response, dict):
        raise ApiDiffFallbackRequired(
            "GitLab API returned an invalid MR changes response."
        )
    if response.get("overflow"):
        raise ApiDiffFallbackRequired("GitLab API reported diff overflow.")

    diff_text = build_unified_diff_from_gitlab_changes(response.get("changes"))

    version_after = latest_mr_version(base_url, token, project_id, mr_iid)
    if not same_mr_version(version_before, version_after):
        raise RuntimeError(
            "Merge Request changed while retrieving its API diff; "
            "stop and wait for a new webhook."
        )
    return diff_text, version_after


def write_diff_metadata(source, version=None, reason=None):
    value = {
        "source": source,
        "source_sha": os.environ.get("EFFECTIVE_SOURCE_SHA", "").lower(),
        "project_id": os.environ.get("EFFECTIVE_PROJECT_ID", ""),
        "mr_iid": os.environ.get("EFFECTIVE_MR_IID", ""),
    }
    if version:
        value["diff_refs"] = version
    if reason:
        value["fallback_reason"] = reason
    write_json("diff-source.json", value)


def import_local_git_diff(path):
    diff_text = read_text(path)
    if not diff_text.strip():
        raise RuntimeError("Local Git fallback produced an empty mr.diff.local.")
    write_text("mr.diff", diff_text)

    reason = "GitLab API diff was unavailable, incomplete, or deliberately bypassed."
    reason_path = "diff-api-fallback-reason.txt"
    if os.path.exists(reason_path):
        reason = read_text(reason_path).strip() or reason

    write_diff_metadata("local_git_fallback", reason=reason)
    print(
        "MR diff prepared from local Git fallback: "
        f"bytes={len(diff_text.encode('utf-8'))}."
    )
