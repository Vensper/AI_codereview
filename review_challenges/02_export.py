"""Read one report chosen by a web request from the export directory."""

from pathlib import Path


def read_export(export_root, report_name):
    return (Path(export_root) / report_name).read_text(encoding="utf-8")
