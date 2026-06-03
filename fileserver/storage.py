import mimetypes
import os
from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from pathlib import PosixPath
from typing import Optional

from fastapi.responses import FileResponse, Response

from fileserver.models import RenderablePath


class StorageBackend(ABC):
    @abstractmethod
    def path_is_dir(self, path: str) -> bool: ...

    @abstractmethod
    def path_is_file(self, path: str) -> bool: ...

    @abstractmethod
    def list_path(self, path: str) -> list[RenderablePath]: ...

    @abstractmethod
    def get_file_response(self, path: str) -> Response: ...

    @abstractmethod
    def file_exists(self, path: str) -> bool: ...

    @abstractmethod
    def write_file(self, path: str, content: bytes) -> None: ...


class LocalStorageBackend(StorageBackend):
    def __init__(self, base_directory: PosixPath):
        self._base = base_directory

    def _resolve(self, path: str) -> PosixPath:
        return self._base / path if path else self._base

    def path_is_dir(self, path: str) -> bool:
        return self._resolve(path).is_dir()

    def path_is_file(self, path: str) -> bool:
        return self._resolve(path).is_file()

    def list_path(self, path: str) -> list[RenderablePath]:
        target = self._resolve(path)
        base_url = f"/{path}/" if path else "/"
        results = []
        with os.scandir(target) as it:
            for entry in it:
                try:
                    entry_stat = entry.stat()
                except OSError:
                    continue
                if entry.is_dir(follow_symlinks=True):
                    results.append(
                        RenderablePath(
                            name=entry.name,
                            url=f"{base_url}{entry.name}/",
                            last_modified=datetime.fromtimestamp(entry_stat.st_mtime),
                            size=entry_stat.st_size,
                            type="Directory",
                        )
                    )
                elif entry.is_file(follow_symlinks=True):
                    results.append(
                        RenderablePath(
                            name=entry.name,
                            url=f"{base_url}{entry.name}",
                            last_modified=datetime.fromtimestamp(entry_stat.st_mtime),
                            size=entry_stat.st_size,
                            type="File",
                        )
                    )
        return results

    def get_file_response(self, path: str) -> Response:
        target = self._resolve(path)
        mime_type, _ = mimetypes.guess_type(target.as_posix())
        return FileResponse(target, media_type=mime_type)

    def file_exists(self, path: str) -> bool:
        return self._resolve(path).is_file()

    def write_file(self, path: str, content: bytes) -> None:
        target = self._resolve(path)
        if not target.parent.is_dir():
            target.parent.mkdir(parents=True)
        with target.open("wb") as fh:
            fh.write(content)


def _build_local_backend() -> Optional[StorageBackend]:
    if tool_data_dir := os.environ.get("TOOL_DATA_DIR"):
        path = PosixPath(tool_data_dir) / "public_html"
        if path.is_dir():
            return LocalStorageBackend(path)

    if home_dir := os.environ.get("HOME"):
        path = PosixPath(home_dir) / "public_html"
        if path.is_dir():
            return LocalStorageBackend(path)

    return None


@lru_cache(maxsize=1)
def get_storage_backend() -> Optional[StorageBackend]:
    return _build_local_backend()
