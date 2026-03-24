"""Core Workspace class for syncing file trees with remote storage."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import fsspec

from temporal_workdir._archive import pack, unpack


def list_workspace_names(prefix_url: str, **storage_options: object) -> list[str]:
    """List workspace names under a URL prefix.

    Returns the stem of each ``.tar.gz`` archive found under the prefix.
    For example, if ``gs://bucket/components/`` contains archives
    ``checkout.tar.gz`` and ``payment.tar.gz``, returns ``["checkout", "payment"]``.

    Args:
        prefix_url: URL prefix to list (e.g., ``gs://bucket/components/``).
        **storage_options: Extra arguments for ``fsspec.filesystem()``.

    Returns:
        Sorted list of workspace names. Empty list if prefix doesn't exist.
    """
    parsed = urlparse(prefix_url.rstrip("/") + "/")
    protocol = parsed.scheme or "file"
    prefix_path = parsed.netloc + parsed.path if parsed.netloc else parsed.path

    fs = fsspec.filesystem(protocol, **storage_options)

    try:
        entries: list[str] = fs.ls(prefix_path, detail=False)
    except FileNotFoundError:
        return []

    names = []
    for entry in entries:
        basename = entry.rsplit("/", 1)[-1]
        if basename.endswith(".tar.gz"):
            names.append(basename.removesuffix(".tar.gz"))
    return sorted(names)


def delete_workspace(remote_url: str, **storage_options: object) -> bool:
    """Delete a workspace archive from remote storage.

    Args:
        remote_url: Remote storage URL (same format as ``Workspace``).
        **storage_options: Extra arguments for ``fsspec.filesystem()``.

    Returns:
        True if the archive existed and was deleted, False if it didn't exist.
    """
    archive_url = remote_url.rstrip("/") + ".tar.gz"
    parsed = urlparse(archive_url)
    protocol = parsed.scheme or "file"
    remote_path = parsed.netloc + parsed.path if parsed.netloc else parsed.path

    fs = fsspec.filesystem(protocol, **storage_options)
    if fs.exists(remote_path):
        fs.rm(remote_path)
        return True
    return False


class Workspace:
    """Sync a local directory with a remote storage location.

    A Workspace maps a remote URL (the "key") to a local directory. On entry,
    the remote archive is downloaded and unpacked. On clean exit, the local
    directory is packed and uploaded back.

    Works with any storage backend supported by fsspec (GCS, S3, Azure, local
    filesystem, etc.). The backend is auto-detected from the URL scheme.

    Usage::

        async with Workspace("gs://bucket/state/component-x") as ws:
            data = json.loads((ws.path / "component.json").read_text())
            (ws.path / "output.csv").write_text("a,b\\n1,2")
            # On clean exit: local dir archived and uploaded to remote

    Args:
        remote_url: Remote storage URL. The scheme determines the fsspec
            backend (``gs://`` for GCS, ``s3://`` for S3, ``file://`` for
            local, etc.). An ``.tar.gz`` suffix is appended automatically
            for the archive file.
        local_path: Local directory to use as the working copy. If ``None``,
            a temporary directory is created.
        cleanup: What to do with the local directory after push.
            ``"auto"`` deletes it, ``"keep"`` leaves it in place.
        read_only: If ``True``, the workspace is pulled but never pushed
            back to remote storage. Useful for reading shared state without
            risking accidental overwrites.
        storage_options: Extra keyword arguments passed to
            ``fsspec.filesystem()``. Use for authentication, project IDs, etc.
    """

    def __init__(
        self,
        remote_url: str,
        local_path: Path | None = None,
        cleanup: Literal["auto", "keep"] = "auto",
        read_only: bool = False,
        **storage_options: object,
    ) -> None:
        self._remote_url = remote_url.rstrip("/")
        self._archive_url = self._remote_url + ".tar.gz"
        self._cleanup = cleanup
        self._read_only = read_only
        self._storage_options = storage_options

        parsed = urlparse(self._archive_url)
        self._protocol = parsed.scheme or "file"
        # fsspec expects path without scheme for most backends
        self._remote_path = (
            parsed.netloc + parsed.path if parsed.netloc else parsed.path
        )

        self._fs = fsspec.filesystem(self._protocol, **storage_options)

        if local_path is not None:
            self._local_path = local_path
            self._owns_tempdir = False
        else:
            self._local_path = Path(tempfile.mkdtemp(prefix="temporal-workdir-"))
            self._owns_tempdir = True

    @property
    def path(self) -> Path:
        """The local working directory.

        Read and write files here freely. Changes are pushed to remote storage
        when the context manager exits cleanly.
        """
        return self._local_path

    async def pull(self) -> None:
        """Download and unpack the remote archive to the local directory.

        If no archive exists at the remote URL, the local directory is left
        empty (first run). Existing local files are removed before unpacking.
        """
        if not self._fs.exists(self._remote_path):
            self._local_path.mkdir(parents=True, exist_ok=True)
            return

        data = self._fs.cat_file(self._remote_path)
        # Clear local dir before unpacking to avoid stale files
        if self._local_path.exists():
            shutil.rmtree(self._local_path)
        unpack(data, self._local_path)

    async def push(self) -> None:
        """Pack the local directory and upload to remote storage.

        If the local directory is empty, the remote archive is deleted
        (if it exists) to keep storage clean.
        """
        files = list(self._local_path.rglob("*"))
        if not any(f.is_file() for f in files):
            # Empty workspace — remove remote archive if it exists
            if self._fs.exists(self._remote_path):
                self._fs.rm(self._remote_path)
            return

        data = pack(self._local_path)
        parent = self._remote_path.rsplit("/", 1)[0]
        self._fs.makedirs(parent, exist_ok=True)
        self._fs.pipe_file(self._remote_path, data)

    async def __aenter__(self) -> Workspace:
        """Pull remote state and return the workspace."""
        await self.pull()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Push local state on clean exit, then optionally clean up."""
        if exc_type is None and not self._read_only:
            await self.push()
        if self._cleanup == "auto" and self._owns_tempdir:
            shutil.rmtree(self._local_path, ignore_errors=True)
