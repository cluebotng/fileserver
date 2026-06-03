import os
from functools import lru_cache
from pathlib import PosixPath
from typing import Optional


@lru_cache(maxsize=1)
def _get_public_html_directory() -> Optional[PosixPath]:
    if tool_data_dir := os.environ.get("TOOL_DATA_DIR"):
        path = PosixPath(tool_data_dir) / "public_html"
        if path.is_dir():
            return path

    if home_dir := os.environ.get("HOME"):
        path = PosixPath(home_dir) / "public_html"
        if path.is_dir():
            return path

    return None


def _have_valid_token(authorization_header: Optional[str], access_key: str) -> bool:
    if authorization_header and " " in authorization_header:
        token = authorization_header.split(" ")[1]
        if access_key == token:
            return True
    return False
