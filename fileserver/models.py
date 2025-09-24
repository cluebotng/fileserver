from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi.staticfiles import StaticFiles


@dataclass(frozen=True)
class RenderablePath:
    name: str
    url: str
    last_modified: datetime
    size: int
    type: str
