from typing import Optional


def _have_valid_token(authorization_header: Optional[str], access_key: str) -> bool:
    if authorization_header and " " in authorization_header:
        token = authorization_header.split(" ")[1]
        if access_key == token:
            return True
    return False
