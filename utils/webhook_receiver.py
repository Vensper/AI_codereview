"""Optional GitLab webhook ingress that preserves MR facts before Jenkins starts.

Run with ``python -m utils.webhook_receiver`` behind an HTTPS reverse proxy.
The GitLab plugin endpoint remains supported; configure only one webhook route
for a given event to avoid duplicate builds.
"""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from utils.mr_context import webhook_context


MAX_BODY_BYTES = 1024 * 1024


def trigger_jenkins(context, environ=None, opener=urllib.request.urlopen):
    values = os.environ if environ is None else environ
    job_url = values.get("JENKINS_JOB_URL", "").rstrip("/")
    user = values.get("JENKINS_API_USER", "")
    token = values.get("JENKINS_API_TOKEN", "")
    if urllib.parse.urlsplit(job_url).scheme not in {"http", "https"} or not user or not token:
        raise RuntimeError("Jenkins job URL or API credentials are not configured.")
    credentials = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
    data = urllib.parse.urlencode({**context, "PUBLISH_COMMENT": "true"}).encode("utf-8")
    request = urllib.request.Request(
        job_url + "/buildWithParameters", data=data, method="POST",
        headers={
            "Authorization": "Basic " + credentials,
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
    )
    with opener(request, timeout=5) as response:
        if response.status not in {200, 201, 202}:
            raise RuntimeError(f"Jenkins rejected the build: HTTP {response.status}.")


class GitLabWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/gitlab":
            self.send_error(404)
            return
        secret = os.environ.get("GITLAB_WEBHOOK_SECRET", "")
        received = self.headers.get("X-Gitlab-Token", "")
        if not secret or not hmac.compare_digest(received, secret):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("Invalid webhook body length.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            context = webhook_context(payload)
            if context is None:
                self.send_response(204)
                self.end_headers()
                return
            allowed_project = os.environ.get("GITLAB_ALLOWED_TARGET_PROJECT_ID", "").strip()
            if allowed_project and context["WEBHOOK_TARGET_PROJECT_ID"] != allowed_project:
                self.send_error(403)
                return
            if not context["WEBHOOK_MR_IID"] and not all(
                context[name] for name in (
                    "WEBHOOK_SOURCE_BRANCH", "WEBHOOK_TARGET_BRANCH", "WEBHOOK_SOURCE_SHA"
                )
            ):
                raise ValueError("Webhook has no MR IID or safe lookup inputs.")
            trigger_jenkins(context)
        except (ValueError, UnicodeError) as exc:
            self.send_error(400, str(exc))
            return
        except (RuntimeError, urllib.error.URLError) as exc:
            self.log_error("Jenkins trigger failed: %s", type(exc).__name__)
            self.send_error(502, "Could not trigger Jenkins.")
            return
        self.send_response(202)
        self.end_headers()


def main():
    host = os.environ.get("WEBHOOK_LISTEN_HOST", "127.0.0.1")
    port = int(os.environ.get("WEBHOOK_LISTEN_PORT", "8097"))
    if not os.environ.get("GITLAB_WEBHOOK_SECRET"):
        raise SystemExit("GITLAB_WEBHOOK_SECRET must be configured.")
    server = ThreadingHTTPServer((host, port), GitLabWebhookHandler)
    print(f"GitLab webhook receiver listening on {host}:{port}/gitlab")
    server.serve_forever()


if __name__ == "__main__":
    main()
