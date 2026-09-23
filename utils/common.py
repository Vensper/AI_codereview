import datetime
import email.utils
import hashlib
import json
import math
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


REVIEWER_VERSION = "v1"
GITLAB_RETRY_STATUSES = {429, *range(500, 600)}
GITLAB_API_ATTEMPTS = 3
GITLAB_API_TIMEOUT_SECONDS = 10


class GitLabAPIError(RuntimeError):
    def __init__(self, message, retryable=False, retry_delay=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_delay = retry_delay


class GitLabPublicationUncertainError(RuntimeError):
    """A POST may have succeeded, but GitLab could not confirm its marker."""


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

    attempts = GITLAB_API_ATTEMPTS if method in {"GET", "PUT"} else 1
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=GITLAB_API_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            retryable = exc.code in GITLAB_RETRY_STATUSES
            delay = _gitlab_retry_delay(exc.headers.get("Retry-After") if exc.headers else None, attempt)
            error = GitLabAPIError(
                f"GitLab API {method} {path} returned HTTP {exc.code}.", retryable, delay
            )
            cause = exc
            exc.close()
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, ConnectionError) as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            retryable = not isinstance(reason, ssl.SSLError)
            delay = _gitlab_retry_delay(None, attempt)
            error = GitLabAPIError(f"Cannot connect to GitLab API: {reason}", retryable, delay)
            cause = exc
        if not error.retryable or attempt + 1 >= attempts or delay is None:
            raise error from cause
        time.sleep(delay)


def _gitlab_retry_delay(retry_after, attempt):
    if retry_after:
        try:
            seconds = float(retry_after)
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(retry_after)
                seconds = (retry_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return None
        if not math.isfinite(seconds) or seconds > 10:
            return None  # Do not exceed the immediate retry budget or ignore Retry-After.
        return max(0, seconds)
    return min(2 ** attempt, 10)


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


def _validated_build_tag(build_tag):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", build_tag or ""):
        raise RuntimeError("BUILD_TAG is required to publish a deduplicated GitLab comment.")
    return build_tag


def _validated_source_sha(source_sha):
    value = str(source_sha or "").lower()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeError("EFFECTIVE_SOURCE_SHA must be a 40-character Git SHA.")
    return value


def failure_note_marker(build_tag):
    return f"<!-- AI_CODE_REVIEW_FAILURE:{_validated_build_tag(build_tag)} -->"


def summary_note_marker(build_tag, source_sha):
    return (
        f"<!-- AI_CODE_REVIEW_SUMMARY:{REVIEWER_VERSION}:"
        f"{_validated_build_tag(build_tag)}:{_validated_source_sha(source_sha)} -->"
    )


def inline_comment_marker(build_tag, source_sha, comment):
    fingerprint_fields = {
        name: comment.get(name)
        for name in (
            "path",
            "side",
            "line",
            "severity",
            "title",
            "problem",
            "risk",
            "suggestion",
        )
    }
    canonical = json.dumps(
        fingerprint_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return (
        f"<!-- AI_CODE_REVIEW_INLINE:{REVIEWER_VERSION}:"
        f"{_validated_build_tag(build_tag)}:{_validated_source_sha(source_sha)}:"
        f"{fingerprint} -->"
    )


def _append_hidden_marker(body, marker):
    return body if marker in body else body.rstrip() + "\n\n" + marker + "\n"


def _find_note_by_marker(base_url, token, project_id, mr_iid, marker, bot_user_id):
    project = urllib.parse.quote(str(project_id), safe="")
    for page in range(1, 21):
        notes = gitlab_api(
            base_url,
            token,
            "GET",
            f"/projects/{project}/merge_requests/{mr_iid}/notes"
            f"?per_page=100&order_by=created_at&sort=desc&page={page}",
        )
        if not isinstance(notes, list):
            raise RuntimeError("GitLab returned an invalid merge request notes response.")
        for note in notes:
            author = note.get("author") if isinstance(note, dict) else None
            if (
                isinstance(author, dict)
                and author.get("id") == bot_user_id
                and marker in str(note.get("body", ""))
            ):
                return note
        if len(notes) < 100:
            return None
    raise RuntimeError("GitLab note lookup exceeded the safe pagination limit.")


def _find_discussion_by_marker(
    base_url, token, project_id, mr_iid, marker, bot_user_id
):
    project = urllib.parse.quote(str(project_id), safe="")
    for page in range(1, 21):
        discussions = gitlab_api(
            base_url,
            token,
            "GET",
            f"/projects/{project}/merge_requests/{mr_iid}/discussions"
            f"?per_page=100&page={page}",
        )
        if not isinstance(discussions, list):
            raise RuntimeError("GitLab returned an invalid merge request discussions response.")
        for discussion in discussions:
            if not isinstance(discussion, dict):
                continue
            notes = discussion.get("notes")
            if not isinstance(notes, list):
                continue
            for note in notes:
                author = note.get("author") if isinstance(note, dict) else None
                if (
                    isinstance(author, dict)
                    and author.get("id") == bot_user_id
                    and marker in str(note.get("body", ""))
                ):
                    return discussion
        if len(discussions) < 100:
            return None
    raise RuntimeError("GitLab discussion lookup exceeded the safe pagination limit.")


def _post_marked_resource(find_existing, post_resource, resource_name):
    """POST with at-most-once behavior while GitLab marker lookup is available."""
    try:
        existing = find_existing()
    except RuntimeError as exc:
        raise GitLabPublicationUncertainError(
            f"Cannot verify an existing {resource_name}; publishing was stopped."
        ) from exc
    if existing is not None:
        return existing, False

    for attempt in range(GITLAB_API_ATTEMPTS):
        try:
            return post_resource(), True
        except GitLabAPIError as exc:
            if not exc.retryable or exc.retry_delay is None:
                raise
            time.sleep(max(exc.retry_delay, min(2 ** attempt, 10)))
            try:
                existing = find_existing()
            except RuntimeError as lookup_error:
                raise GitLabPublicationUncertainError(
                    f"The {resource_name} POST may have succeeded, but its marker "
                    "could not be verified; publishing was stopped to avoid duplicates."
                ) from lookup_error
            if existing is not None:
                return existing, False
            if attempt + 1 == GITLAB_API_ATTEMPTS:
                raise

    raise AssertionError("Marked GitLab POST retry loop exited unexpectedly.")


def post_marked_note(
    base_url, token, project_id, mr_iid, body, marker, bot_user_id=None
):
    bot_user_id = bot_user_id or current_gitlab_user_id(base_url, token)
    note_body = _append_hidden_marker(body, marker)
    return _post_marked_resource(
        lambda: _find_note_by_marker(
            base_url, token, project_id, mr_iid, marker, bot_user_id
        ),
        lambda: post_summary_note(base_url, token, project_id, mr_iid, note_body),
        "merge request note",
    )


def post_review_summary_note(
    base_url, token, project_id, mr_iid, body, build_tag, source_sha, bot_user_id=None
):
    marker = summary_note_marker(build_tag, source_sha)
    note, _created = post_marked_note(
        base_url, token, project_id, mr_iid, body, marker, bot_user_id
    )
    return note


def post_marked_discussion(
    base_url, token, project_id, mr_iid, form, marker, bot_user_id=None
):
    bot_user_id = bot_user_id or current_gitlab_user_id(base_url, token)
    project = urllib.parse.quote(str(project_id), safe="")
    marked_form = dict(form)
    marked_form["body"] = _append_hidden_marker(str(form.get("body", "")), marker)
    discussion, _created = _post_marked_resource(
        lambda: _find_discussion_by_marker(
            base_url, token, project_id, mr_iid, marker, bot_user_id
        ),
        lambda: gitlab_api(
            base_url,
            token,
            "POST",
            f"/projects/{project}/merge_requests/{mr_iid}/discussions",
            marked_form,
        ),
        "merge request discussion",
    )
    return discussion


def post_failure_note(base_url, token, project_id, mr_iid, body, build_tag):
    marker = failure_note_marker(build_tag)
    note, created = post_marked_note(
        base_url, token, project_id, mr_iid, body, marker
    )
    return note if created else None


def publishing_enabled():
    return os.environ.get(
        "REVIEW_PUBLISH_COMMENT", os.environ.get("PUBLISH_COMMENT", "false")
    ).lower() in {
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
    bot_user_id = current_gitlab_user_id(base_url, token)
    marker = review_done_marker(source_sha)
    return _find_note_by_marker(
        base_url, token, project_id, mr_iid, marker, bot_user_id
    ) is not None


def update_mr_note(base_url, token, project_id, mr_iid, note_id, body):
    project = urllib.parse.quote(str(project_id), safe="")
    return gitlab_api(
        base_url,
        token,
        "PUT",
        f"/projects/{project}/merge_requests/{mr_iid}/notes/{note_id}",
        {"body": body},
    )
