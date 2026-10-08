import json
import os

from utils.common import write_text
from utils.review_result import normalize_review, validate_json_schema


REVIEW_SCHEMA_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "review.schema.json")
)
STRUCTURED_REVIEW_ATTEMPTS = 2


def load_review_schema(path=REVIEW_SCHEMA_PATH):
    """Load the single JSON contract shared by every AI executor."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            schema = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("无法读取 review.schema.json。") from exc
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise RuntimeError("review.schema.json 的根 schema 必须是 object。")
    return schema


def validate_review_text(raw_review, schema):
    """Validate an executor response and normalize it for publication."""
    try:
        value = json.loads(raw_review)
    except json.JSONDecodeError as exc:
        raise ValueError("Structured review is not valid JSON.") from exc
    validate_json_schema(value, schema)
    if not value["summary"].strip() or not value["change_intent"].strip():
        raise ValueError("Structured review summary and change_intent cannot be blank.")
    for index, change in enumerate(value["changes"]):
        if not change["path"].strip() or not change["description"].strip():
            raise ValueError(f"Structured review changes[{index}] contains blank text.")
    comment_text_fields = ("title", "path", "problem", "risk", "suggestion")
    for index, comment in enumerate(value["comments"]):
        if any(not comment[name].strip() for name in comment_text_fields):
            raise ValueError(f"Structured review comments[{index}] contains blank text.")
    return normalize_review(value)


def generate_review_with_retry(
    generate,
    schema,
    initial_draft="",
    write_text_fn=write_text,
    check_deadline=None,
):
    """Generate and validate a structured review, retrying once on any bad result.

    ``generate`` receives ``(attempt, previous_output, validation_error)`` and
    must return the raw JSON text produced by an executor. Keeping retries here
    makes the publication contract identical for Claude Code and legacy APIs.
    """
    if isinstance(initial_draft, str) and initial_draft.strip():
        write_text_fn("review-raw-initial.txt", initial_draft.strip() + "\n")

    previous_output = None
    validation_error = None
    last_error = None
    for attempt in range(STRUCTURED_REVIEW_ATTEMPTS):
        if check_deadline is not None:
            check_deadline()
        raw_structured = None
        try:
            raw_structured = generate(attempt, previous_output, validation_error)
            if not isinstance(raw_structured, str) or not raw_structured.strip():
                raise ValueError("AI executor returned no structured output.")
            raw_structured = raw_structured.strip()
            write_text_fn("review-raw.txt", raw_structured + "\n")
            if check_deadline is not None:
                check_deadline()
            review = validate_review_text(raw_structured, schema)
            if check_deadline is not None:
                check_deadline()
            write_text_fn("review-validation.txt", "valid\n")
            return review
        except ValueError as exc:
            last_error = exc
            validation_error = str(exc)
            write_text_fn("review-validation.txt", f"invalid: {exc}\n")
            if attempt == 0 and raw_structured is not None:
                previous_output = raw_structured
                write_text_fn(
                    "review-raw-structured-initial.txt",
                    raw_structured.rstrip() + "\n",
                )

    raise RuntimeError(
        "AI structured output failed after retry; "
        "see review-validation.txt and review-raw.txt."
    ) from last_error
