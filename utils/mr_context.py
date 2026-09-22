"""Resolve an MR only when the event or GitLab API identifies it unambiguously."""

import os
import re
import urllib.parse

from utils.common import gitlab_api, gitlab_base_url


SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")


def project_path_from_url(repository_url):
    parsed = urllib.parse.urlsplit(repository_url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("GitLab repository URL is unavailable or invalid.")
    path = urllib.parse.unquote(parsed.path).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise RuntimeError("GitLab repository path is invalid.")
    return path


def _positive_id(value, name):
    value = str(value or "").strip()
    if not value.isdecimal() or int(value) < 1:
        raise RuntimeError(f"{name} is unavailable or invalid.")
    return value


def resolve_mr_identity(environment=None, api=gitlab_api):
    """Return (target project ID, MR IID); never pick an ambiguous MR."""
    values = os.environ if environment is None else environment
    project = str(values.get("EFFECTIVE_PROJECT_ID", "")).strip()
    iid = str(values.get("EFFECTIVE_MR_IID", "")).strip()
    if project and iid:
        return _positive_id(project, "Target project ID"), _positive_id(iid, "MR IID")

    target_url = str(values.get("EFFECTIVE_TARGET_REPOSITORY_URL", "")).strip()
    source_url = str(values.get("EFFECTIVE_REPOSITORY_URL", "")).strip()
    base_url = gitlab_base_url(target_url or source_url)
    token = str(values.get("GITLAB_API_TOKEN", "")).strip()
    if not token:
        raise RuntimeError("GitLab API token is unavailable for MR lookup.")

    if not project:
        if not target_url:
            raise RuntimeError("Target project is unavailable; MR lookup is unsafe.")
        project = project_path_from_url(target_url)
    if not project.isdecimal():
        path = urllib.parse.quote(project, safe="")
        response = api(base_url, token, "GET", f"/projects/{path}")
        if not isinstance(response, dict):
            raise RuntimeError("GitLab returned an invalid target project response.")
        project = _positive_id(response.get("id"), "Target project ID")
    else:
        project = _positive_id(project, "Target project ID")
    if iid:
        return project, _positive_id(iid, "MR IID")

    source_branch = str(values.get("EFFECTIVE_SOURCE_BRANCH", "")).strip()
    target_branch = str(values.get("EFFECTIVE_TARGET_BRANCH", "")).strip()
    source_sha = str(values.get("EFFECTIVE_SOURCE_SHA", "")).strip().lower()
    if not source_branch or not target_branch or not SHA_RE.fullmatch(source_sha):
        raise RuntimeError("Source branch, target branch, and source SHA are required for MR lookup.")

    source_project_id = str(values.get("EFFECTIVE_SOURCE_PROJECT_ID", "")).strip()
    if source_project_id:
        source_project_id = _positive_id(source_project_id, "Source project ID")
    else:
        source_path = project_path_from_url(source_url)
        path = urllib.parse.quote(source_path, safe="")
        response = api(base_url, token, "GET", f"/projects/{path}")
        if not isinstance(response, dict):
            raise RuntimeError("GitLab returned an invalid source project response.")
        source_project_id = _positive_id(response.get("id"), "Source project ID")

    query = urllib.parse.urlencode(
        {"state": "opened", "source_branch": source_branch, "target_branch": target_branch,
         "per_page": 100}
    )
    matches = []
    for page in range(1, 21):
        response = api(
            base_url, token, "GET",
            f"/projects/{project}/merge_requests?{query}&page={page}",
        )
        if not isinstance(response, list):
            raise RuntimeError("GitLab returned an invalid merge request list.")
        for mr in response:
            if not isinstance(mr, dict):
                continue
            if (mr.get("state") != "opened"
                or mr.get("source_branch") != source_branch
                or mr.get("target_branch") != target_branch
                or str(mr.get("source_project_id")) != source_project_id
                or str(mr.get("target_project_id")) != project):
                continue
            if str(mr.get("sha", "")).lower() != source_sha:
                continue
            matches.append(mr)
        if len(response) < 100:
            break
    else:
        raise RuntimeError("GitLab MR lookup exceeded the safe pagination limit.")

    if len(matches) != 1:
        raise RuntimeError(f"GitLab MR lookup found {len(matches)} exact matches; refusing to guess.")
    return project, _positive_id(matches[0].get("iid"), "MR IID")


def webhook_context(payload):
    """Extract only review events and pass original MR facts to Jenkins."""
    if not isinstance(payload, dict):
        raise ValueError("Webhook body must be a JSON object.")
    kind = payload.get("object_kind")
    if kind == "note":
        note = payload.get("object_attributes") or {}
        if not isinstance(note, dict):
            raise ValueError("Webhook note data is invalid.")
        if note.get("noteable_type") != "MergeRequest" or str(note.get("note", "")).strip() != "/codereview":
            return None
        mr = payload.get("merge_request") or {}
    elif kind == "merge_request":
        mr = payload.get("object_attributes") or {}
        if not isinstance(mr, dict):
            raise ValueError("Webhook merge request data is invalid.")
        action = mr.get("action")
        if mr.get("state") != "opened" or not (
            action == "open" or (action == "update" and SHA_RE.fullmatch(str(mr.get("oldrev") or "")))
        ):
            return None
    else:
        return None
    if not isinstance(mr, dict):
        raise ValueError("Webhook merge request data is invalid.")
    source = mr.get("source") or {}
    target = mr.get("target") or {}
    last_commit = mr.get("last_commit") or {}
    project = payload.get("project") or {}
    source_project = str(mr.get("source_project_id") or "")
    target_project = str(mr.get("target_project_id") or project.get("id") or "")
    same_project = bool(source_project and target_project and source_project == target_project)
    source_repo_url = source.get("git_http_url") or source.get("http_url")
    if not source_repo_url and same_project:
        source_repo_url = project.get("git_http_url")
    result = {
        "WEBHOOK_EVENT_KIND": kind,
        "WEBHOOK_MR_IID": str(mr.get("iid") or ""),
        "WEBHOOK_TARGET_PROJECT_ID": str(mr.get("target_project_id") or project.get("id") or ""),
        "WEBHOOK_SOURCE_PROJECT_ID": str(mr.get("source_project_id") or ""),
        "WEBHOOK_SOURCE_BRANCH": str(mr.get("source_branch") or ""),
        "WEBHOOK_TARGET_BRANCH": str(mr.get("target_branch") or ""),
        "WEBHOOK_SOURCE_REPO_HTTP_URL": str(source_repo_url or ""),
        "WEBHOOK_TARGET_REPO_HTTP_URL": str(target.get("git_http_url") or target.get("http_url") or project.get("git_http_url") or ""),
        "WEBHOOK_SOURCE_SHA": str(last_commit.get("id") or ""),
    }
    for key in ("WEBHOOK_MR_IID", "WEBHOOK_TARGET_PROJECT_ID", "WEBHOOK_SOURCE_PROJECT_ID"):
        if result[key] and (not result[key].isdecimal() or int(result[key]) < 1):
            raise ValueError(f"{key} is invalid.")
    if result["WEBHOOK_SOURCE_SHA"] and not SHA_RE.fullmatch(result["WEBHOOK_SOURCE_SHA"]):
        raise ValueError("Webhook source SHA is invalid.")
    return result
