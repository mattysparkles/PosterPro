import hashlib

import numpy as np


def fake_clip_embedding(path: str, dim: int = 32) -> list[float]:
    """Deterministic placeholder embedding for MVP scaffolding."""
    digest = hashlib.sha256(path.encode()).digest()
    arr = np.frombuffer(digest, dtype=np.uint8)[:dim].astype(np.float32)
    norm = np.linalg.norm(arr) or 1.0
    return (arr / norm).tolist()
