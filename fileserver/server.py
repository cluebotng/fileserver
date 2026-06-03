import asyncio
import os
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from fileserver.storage import StorageBackend, get_storage_backend
from fileserver.utils import _have_valid_token


_jinja_env = Environment(
    loader=PackageLoader("fileserver"),
    autoescape=select_autoescape(),
)
_listing_template = _jinja_env.get_template("index.html")


def _get_storage() -> StorageBackend:
    storage = get_storage_backend()
    if storage is None:
        raise RuntimeError("No storage backend available")
    return storage


def _render_listing(storage: StorageBackend, path: str) -> HTMLResponse:
    stripped = path.strip("/")
    current_path = f"/{stripped}/" if stripped else "/"
    if stripped:
        parent = stripped.rsplit("/", 1)[0]
        parent_url = f"/{parent}/" if parent else "/"
    else:
        parent_url = None

    paths = storage.list_path(stripped)
    return HTMLResponse(
        _listing_template.render(
            parent_url=parent_url,
            current_path=current_path,
            paths=sorted(paths, key=lambda x: (x.type, x.name)),
        )
    )


app = FastAPI()

if write_api_key := os.environ.get("FILE_API_KEY"):

    @app.put("/{path:path}", response_class=HTMLResponse)
    @app.post("/{path:path}", response_class=HTMLResponse)
    async def put_file(path: str, request: Request):
        storage = _get_storage()

        if not _have_valid_token(request.headers.get("Authorization"), write_api_key):
            return HTMLResponse(status_code=403)

        if await asyncio.to_thread(storage.file_exists, path):
            # File already exists, don't overwrite it
            # Return 200 to make the client not treat this as a failure
            return HTMLResponse(status_code=200)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)

        async def fill_queue():
            async for chunk in request.stream():
                await queue.put(chunk)
            await queue.put(None)

        def sync_chunks():
            while True:
                chunk = asyncio.run_coroutine_threadsafe(queue.get(), loop).result()
                if chunk is None:
                    return
                yield chunk

        fill_task = asyncio.create_task(fill_queue())
        await asyncio.to_thread(storage.write_file, path, sync_chunks())
        await fill_task
        return HTMLResponse(status_code=201)


@app.get("/_/health", response_class=HTMLResponse)
async def health_check():
    return HTMLResponse(status_code=200)


@app.get("/", response_class=HTMLResponse)
@app.get("/{path:path}", response_class=HTMLResponse)
async def list_files(path: Optional[str] = None):
    storage = _get_storage()
    path = path or ""

    if await asyncio.to_thread(storage.path_is_dir, path):
        return await asyncio.to_thread(_render_listing, storage, path)

    if await asyncio.to_thread(storage.path_is_file, path):
        return await asyncio.to_thread(storage.get_file_response, path)

    return HTMLResponse(status_code=404)
