"""Single-file snapshot of a data directory (.psnap, a zip underneath)."""

from __future__ import annotations

import os
import zipfile

SUFFIX = ".psnap"
ENTRIES = ("graph.json", "vectors.json", "状态", "说法")


def create(data_dir: str, out_path: str) -> str:
    if not out_path.lower().endswith(SUFFIX):
        out_path += SUFFIX
    directory = os.path.abspath(data_dir)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in ENTRIES:
            path = os.path.join(directory, entry)
            if os.path.isfile(path):
                archive.write(path, entry)
            elif os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for filename in files:
                        full = os.path.join(root, filename)
                        archive.write(full, os.path.relpath(full, directory))
    return out_path


def restore(snapshot: str, to_dir: str) -> str:
    target = os.path.abspath(to_dir)
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(snapshot) as archive:
        archive.extractall(target)
    return target
