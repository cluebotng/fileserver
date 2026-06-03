from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RenderablePath:
    name: str
    url: str
    last_modified: datetime
    size: int
    type: str
