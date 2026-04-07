from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import Cluster, Image, Listing
from app.services.clustering import cluster_embeddings
from app.workers.celery_app import celery_app


@celery_app.task(name="cluster_images")
def cluster_images_task(user_id: int) -> dict:
    with SessionLocal() as db:
        images = db.execute(select(Image).where(Image.user_id == user_id)).scalars().all()
        groups = cluster_embeddings([(img.id, img.embedding or []) for img in images if img.embedding])
        result = {}
        for _, image_ids in groups.items():
            cluster = Cluster(user_id=user_id)
            db.add(cluster)
            db.flush()
            for image_id in image_ids:
                image = next(i for i in images if i.id == image_id)
                image.cluster_id = cluster.id
            listing = Listing(user_id=user_id, cluster_id=cluster.id, status="draft")
            db.add(listing)
            result[str(cluster.id)] = image_ids
        db.commit()
        return result
