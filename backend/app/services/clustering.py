from collections import defaultdict

import numpy as np
from sklearn.cluster import DBSCAN


def cluster_embeddings(image_rows: list[tuple[int, list[float]]]) -> dict[int, list[int]]:
    if not image_rows:
        return {}
    ids = [row[0] for row in image_rows]
    matrix = np.array([row[1] for row in image_rows], dtype=np.float32)
    labels = DBSCAN(metric="cosine", eps=0.15, min_samples=1).fit_predict(matrix)
    grouped: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        grouped[int(label)].append(ids[idx])
    return grouped
