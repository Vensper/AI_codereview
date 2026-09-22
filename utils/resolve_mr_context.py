"""Jenkins entry point for the safe GitLab MR lookup fallback."""

import os
import sys

from utils.mr_context import resolve_mr_identity


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m utils.resolve_mr_context <output-file>")
    project_id, mr_iid = resolve_mr_identity()
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as output:
        output.write(f"{project_id}\n{mr_iid}\n")
    print("MR identity resolved and checked for uniqueness.")


if __name__ == "__main__":
    main()
