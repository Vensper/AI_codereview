"""Deprecated command-line adapter for the pre-split review entry point.

Jenkins deliberately does not invoke this file. It keeps the former commands
working for manual troubleshooting while delegating all work to stage modules.
"""

import sys
from pathlib import Path


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import ai_review, prepare_api_diff, publish_review, report_review_failure
from utils import review_start, use_local_git_diff


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--review"
    if mode == "--start":
        review_start.main()
    elif mode == "--prepare-api-diff":
        return prepare_api_diff.main()
    elif mode == "--use-local-diff":
        if len(sys.argv) != 3:
            raise SystemExit("Usage: review.py --use-local-diff <path>")
        use_local_git_diff.import_local_git_diff(sys.argv[2])
    elif mode == "--review":
        ai_review.main()
        publish_review.main()
    elif mode == "--fail":
        report_review_failure.main()
    else:
        raise SystemExit(
            "Usage: review.py [--start|--prepare-api-diff|"
            "--use-local-diff <path>|--review|--fail]"
        )


if __name__ == "__main__":
    raise SystemExit(main())
