import json
import os
import urllib.error
import urllib.parse
import urllib.request


REVIEWER_VERSION = "v1"


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not available.")
    return value


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def write_text(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def write_json(path, value):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def gitlab_api(base_url, token, method, path, form=None):
    url = base_url.rstrip("/") + "/api/v4" + path
    data = None
    headers = {"PRIVATE-TOKEN": token, "Accept": "application/json"}
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"GitLab API {method} {path} returned HTTP {exc.code}."
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot connect to GitLab API: {exc.reason}") from exc


def gitlab_base_url(repository_url):
    configured = os.environ.get("GITLAB_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    parsed = urllib.parse.urlparse(repository_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            "Cannot derive GitLab URL from repository URL; set GITLAB_BASE_URL."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def post_summary_note(base_url, token, project_id, mr_iid, body):
    project = urllib.parse.quote(str(project_id), safe="")
    return gitlab_api(
        base_url,
        token,
        "POST",
        f"/projects/{project}/merge_requests/{mr_iid}/notes",
        {"body": body},
    )


def publishing_enabled():
    return os.environ.get("PUBLISH_COMMENT", "false").lower() in {
        "1", "true", "yes"
    }


def publishing_context():
    repository_url = required_env("EFFECTIVE_REPOSITORY_URL")
    project_id = required_env("EFFECTIVE_PROJECT_ID")
    mr_iid = required_env("EFFECTIVE_MR_IID")
    token = required_env("GITLAB_API_TOKEN")
    return gitlab_base_url(repository_url), token, project_id, mr_iid


def update_mr_labels(base_url, token, project_id, mr_iid, add, remove):
    project = urllib.parse.quote(str(project_id), safe="")
    return gitlab_api(
        base_url,
        token,
        "PUT",
        f"/projects/{project}/merge_requests/{mr_iid}",
        {"add_labels": ",".join(add), "remove_labels": ",".join(remove)},
    )


def review_done_marker(source_sha):
    return f"<!-- AI_CODE_REVIEW_DONE:{REVIEWER_VERSION}:{source_sha.lower()} -->"


def current_gitlab_user_id(base_url, token):
    user = gitlab_api(base_url, token, "GET", "/user")
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(user_id, int):
        raise RuntimeError("GitLab API token cannot identify its current user.")
    return user_id


def has_completed_review(base_url, token, project_id, mr_iid, source_sha):
    """Only trust a completion marker written by this API token's bot user."""
    project = urllib.parse.quote(str(project_id), safe="")
    notes = gitlab_api(
        base_url,
        token,
        "GET",
        f"/projects/{project}/merge_requests/{mr_iid}/notes?per_page=100&order_by=created_at&sort=desc",
    )
    if not isinstance(notes, list):
        raise RuntimeError("GitLab returned an invalid merge request notes response.")

    bot_user_id = current_gitlab_user_id(base_url, token)
    marker = review_done_marker(source_sha)
    for note in notes:
        if not isinstance(note, dict):
            continue
        author = note.get("author")
        if not isinstance(author, dict) or author.get("id") != bot_user_id:
            continue
        if marker in str(note.get("body", "")):
            return True
    return False


def update_mr_note(base_url, token, project_id, mr_iid, note_id, body):
    project = urllib.parse.quote(str(project_id), safe="")
    return gitlab_api(
        base_url,
        token,
        "PUT",
        f"/projects/{project}/merge_requests/{mr_iid}/notes/{note_id}",
        {"body": body},
    )
