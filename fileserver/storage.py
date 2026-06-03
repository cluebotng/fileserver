import io
import logging
import mimetypes
import os
from abc import ABC, abstractmethod
from datetime import datetime
from functools import lru_cache
from pathlib import PosixPath
from typing import Optional
from keystoneauth1 import session
from keystoneauth1.identity import v3
from swiftclient import Connection
from swiftclient.exceptions import ClientException

from starlette.responses import FileResponse, Response
from fileserver.models import RenderablePath

logger = logging.getLogger(__name__)


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


class SwiftStorageBackend(StorageBackend):
    def __init__(self, container: str, connection):
        self._container = container
        self._conn = connection

    def _prefix(self, path: str) -> str:
        stripped = path.strip("/")
        return f"{stripped}/" if stripped else ""

    def path_is_dir(self, path: str) -> bool:
        if not path or not path.strip("/"):
            return True
        prefix = self._prefix(path)
        try:
            _, objects = self._conn.get_container(
                self._container, prefix=prefix, delimiter="/", limit=1
            )
            return len(objects) > 0
        except ClientException as e:
            if e.http_status == 404:
                return False
            logger.exception("Error checking if path is dir: %s", path)
            return False
        except Exception:
            logger.exception("Error checking if path is dir: %s", path)
            return False

    def path_is_file(self, path: str) -> bool:
        try:
            self._conn.head_object(self._container, path.strip("/"))
            return True
        except ClientException as e:
            if e.http_status == 404:
                return False
            logger.exception("Error checking if path is file: %s", path)
            return False
        except Exception:
            logger.exception("Error checking if path is file: %s", path)
            return False

    def list_path(self, path: str) -> list[RenderablePath]:
        prefix = self._prefix(path)
        try:
            _, objects = self._conn.get_container(
                self._container, prefix=prefix, delimiter="/"
            )
        except Exception:
            logger.exception("Error listing path: %s", path)
            return []

        results = []
        for obj in objects:
            if "subdir" in obj:
                subdir = obj["subdir"].rstrip("/")
                name = subdir.rsplit("/", 1)[-1]
                results.append(
                    RenderablePath(
                        name=name,
                        url=f"/{subdir}/",
                        last_modified=datetime.min,
                        size=0,
                        type="Directory",
                    )
                )
            else:
                name = obj["name"].removeprefix(prefix)
                if not name:
                    continue
                results.append(
                    RenderablePath(
                        name=name,
                        url=f"/{obj['name']}",
                        last_modified=datetime.fromisoformat(
                            obj["last_modified"].replace("Z", "+00:00")
                        ),
                        size=obj["bytes"],
                        type="File",
                    )
                )
        return results

    def get_file_response(self, path: str) -> Response:
        try:
            headers, content = self._conn.get_object(self._container, path.strip("/"))
        except Exception:
            logger.exception("Error getting file: %s", path)
            return Response(status_code=404)
        content_type = headers.get("content-type") or mimetypes.guess_type(path)[0]
        return Response(content=content, media_type=content_type)

    def file_exists(self, path: str) -> bool:
        return self.path_is_file(path)

    def write_file(self, path: str, content: bytes) -> None:
        obj_name = path.strip("/")
        mime_type, _ = mimetypes.guess_type(obj_name)
        try:
            self._conn.put_object(
                self._container,
                obj_name,
                contents=io.BytesIO(content),
                content_length=len(content),
                content_type=mime_type or "application/octet-stream",
            )
        except Exception:
            logger.exception("Error writing file: %s", path)
            return Response(status_code=500)


def _build_swift_backend() -> Optional[StorageBackend]:
    auth_url = os.environ.get("SWIFT_AUTH_URL")
    container_name = os.environ.get("SWIFT_CONTAINER_NAME")
    application_credential_id = os.environ.get("SWIFT_APPLICATION_CREDENTIAL_ID")
    application_credential_secret = os.environ.get(
        "SWIFT_APPLICATION_CREDENTIAL_SECRET"
    )

    if (
        auth_url
        and container_name
        and application_credential_id
        and application_credential_secret
    ):
        keystone_session = session.Session(
            auth=v3.ApplicationCredential(
                auth_url=auth_url,
                application_credential_id=application_credential_id,
                application_credential_secret=application_credential_secret,
            )
        )

        return SwiftStorageBackend(
            container=container_name,
            connection=Connection(session=keystone_session),
        )
    return None


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
    return _build_swift_backend() or _build_local_backend()
