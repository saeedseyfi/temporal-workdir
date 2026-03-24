"""Remote workspace sync for Temporal activities.

This package provides a :class:`Workspace` that syncs a local directory with
remote storage (GCS, S3, Azure, local, etc.) before and after a Temporal
activity executes. This enables file-based activities to work correctly across
distributed workers where disk state is not shared.

The storage backend is auto-detected from the URL scheme via `fsspec`_.

.. _fsspec: https://filesystem-spec.readthedocs.io/
"""

from temporal_workdir._temporal import get_workspace_path, workspace
from temporal_workdir._workspace import (
    Workspace,
    delete_workspace,
    list_workspace_names,
)

__all__ = [
    "Workspace",
    "delete_workspace",
    "get_workspace_path",
    "list_workspace_names",
    "workspace",
]
