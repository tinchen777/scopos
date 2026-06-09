
from typing import Any


def fmt_gb(num_bytes: float, integer: Any = "", decimal: Any = 2) -> str:
    return f"{num_bytes / (1024 ** 3):{integer}.{decimal}f}"
