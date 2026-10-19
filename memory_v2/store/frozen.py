"""Per-session frozen graph/vector copy and the global frozen version.

The in-flight session reads the network as it was *before* the session started.
The live graph is still maintained during the session; replay/archive writes the
live graph back as the next frozen version for later sessions.
"""

from __future__ import annotations

import os
import shutil

from .graph import Graph
from .vectors import VectorStore

GLOBAL_NAME = "冻结"


def global_dir(data_dir: str) -> str:
    return os.path.join(os.path.abspath(data_dir), GLOBAL_NAME)


def _copy_file(source: str, target: str) -> None:
    if os.path.exists(source):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = target + ".tmp"
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    elif os.path.exists(target):
        os.remove(target)


def update_global(data_dir: str) -> str:
    """Copy the current live graph/vectors to the global frozen version."""
    source_dir = os.path.abspath(data_dir)
    target_dir = global_dir(source_dir)
    os.makedirs(target_dir, exist_ok=True)
    for name in ("graph.json", "vectors.json"):
        _copy_file(os.path.join(source_dir, name), os.path.join(target_dir, name))
    return target_dir


class FrozenSession:
    def __init__(self, data_dir: str, session: str):
        self.data_dir = os.path.abspath(data_dir)
        self.session = str(session)
        self.dir = os.path.join(self.data_dir, "状态", self.session + ".frozen")
        self.graph_path = os.path.join(self.dir, "graph.json")
        self.vectors_path = os.path.join(self.dir, "vectors.json")

    def exists(self) -> bool:
        return os.path.exists(self.graph_path)

    def clone_from(self, source_dir: str) -> None:
        os.makedirs(self.dir, exist_ok=True)
        for name in ("graph.json", "vectors.json"):
            _copy_file(os.path.join(source_dir, name),
                       os.path.join(self.dir, name))

    def clone_global(self) -> None:
        source_dir = global_dir(self.data_dir)
        if os.path.isdir(source_dir):
            self.clone_from(source_dir)
        else:
            self.clone_from(os.path.join(self.data_dir, "__no_global__"))

    def load(self, model: str, dim: int):
        return (Graph(self.graph_path),
                VectorStore(self.vectors_path, model=model, dim=dim))
