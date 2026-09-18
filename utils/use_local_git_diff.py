import sys

from utils.diff_utils import import_local_git_diff


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m utils.use_local_git_diff <path>")
    import_local_git_diff(sys.argv[1])


if __name__ == "__main__":
    main()
