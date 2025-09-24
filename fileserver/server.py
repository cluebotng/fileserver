import mimetypes
import os
from datetime import datetime
from pathlib import PosixPath
from typing import Optional

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse, FileResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from fileserver.models import RenderablePath
from fileserver.utils import _get_public_html_directory, _have_valid_token


def _render_listing(base_directory: PosixPath, target_path: PosixPath) -> None:
    paths = set()
    for path in target_path.iterdir():
        if path.is_dir():
            stat = path.lstat()
            paths.add(
                RenderablePath(
                    name=path.name,
                    url=f"/{path.relative_to(base_directory).as_posix()}/",
                    last_modified=datetime.fromtimestamp(stat.st_mtime),
                    size=stat.st_size,
                    type="Directory",
                )
            )
        if path.is_file():
            stat = path.lstat()
            paths.add(
                RenderablePath(
                    name=path.name,
                    url=f"/{path.relative_to(base_directory).as_posix()}",
                    last_modified=datetime.fromtimestamp(stat.st_mtime),
                    size=stat.st_size,
                    type="File",
                )
            )

    template = Environment(
        loader=PackageLoader("fileserver"),
        autoescape=select_autoescape(),
    ).get_template("index.html")

    current_path_name = target_path.relative_to(base_directory).name
    return HTMLResponse(
        template.render(
            parent_url=(
                None
                if current_path_name in {".", ""}
                else f"/{target_path.parent.relative_to(base_directory).as_posix()}/"
            ),
            current_path=(
                "/"
                if current_path_name in {".", ""}
                else f"/{target_path.relative_to(base_directory).as_posix()}/"
            ),
            paths=sorted(paths, key=lambda x: (x.type, x.name)),
        )
    )


app = FastAPI()

if write_api_key := os.environ.get("FILE_API_KEY"):

    @app.get("/{path:path}", response_class=HTMLResponse)
    async def put_file(path: str, request: Request):
        base_directory = _get_public_html_directory()
        if base_directory is None:
            raise RuntimeError("Failed to find public_html directory")

        if not _have_valid_token(request.headers.get("Authorization"), write_api_key):
            return HTMLResponse(status_code=403)

        target_path = base_directory / path if path else base_directory

        if not target_path.parent.is_dir():
            target_path.parent.mkdir(parents=True)

        if target_path.is_file():
            # File already exists, don't overwrite it
            # Return 200 to make the client not treat this as a failure, assume the content is the same
            return HTMLResponse(status_code=200)

        with target_path.open("wb") as fh:
            fh.write(await request.body())

        return HTMLResponse(status_code=201)


@app.get("/", response_class=HTMLResponse)
@app.get("/{path:path}", response_class=HTMLResponse)
async def list_files(path: Optional[str] = None):
    base_directory = _get_public_html_directory()
    if base_directory is None:
        raise RuntimeError("Failed to find public_html directory")

    target_path = base_directory / path if path else base_directory

    if target_path.is_dir():
        return _render_listing(base_directory, target_path)

    if target_path.is_file():
        mime_type, _ = mimetypes.guess_type(target_path.as_posix())
        return FileResponse(target_path, media_type=mime_type)

    return HTMLResponse(status_code=404)
