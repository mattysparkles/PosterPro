from pathlib import Path

from PIL import Image

from app.models.models import MediaRecoveryItemGroup, MediaRecoveryMedia, User
from app.services.media_recovery import MediaRecoveryService


def _image(path: Path, color: str, size: tuple[int, int] = (160, 120)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color=color).save(path)


def test_manifest_preserves_paths_and_marks_exact_duplicates(db_session, tmp_path):
    user = User(email="media-recovery@example.com")
    db_session.add(user)
    db_session.commit()
    root = tmp_path / "storage"
    item = root / "intake-items" / "SP-20260708-0008"
    _image(item / "first.jpg", "red")
    (root / "intake-google-photos").mkdir(parents=True)
    (root / "intake-google-photos" / "same.jpg").write_bytes((item / "first.jpg").read_bytes())

    service = MediaRecoveryService()
    run = service.manifest(db_session, user=user, roots=[root / "intake-items", root / "intake-google-photos"], run_key="manifest-test")

    rows = db_session.query(MediaRecoveryMedia).filter_by(run_id=run.id).all()
    assert len(rows) == 2
    assert len({row.absolute_path for row in rows}) == 2
    assert len({row.sha256 for row in rows}) == 1
    assert sum(row.duplicate_of_media_id is not None for row in rows) == 1


def test_grouping_keeps_large_directory_and_unassigned_media_in_review(db_session, tmp_path):
    user = User(email="media-grouping@example.com")
    db_session.add(user)
    db_session.commit()
    root = tmp_path / "storage"
    item = root / "intake-items" / "SP-20260708-0009"
    for index in range(41):
        _image(item / f"photo-{index}.jpg", f"#{index:02x}0000", size=(160 + index, 120 + index))
    _image(root / "intake-google-photos" / "unassigned.jpg", "blue")

    service = MediaRecoveryService()
    run = service.manifest(db_session, user=user, roots=[root / "intake-items", root / "intake-google-photos"], run_key="group-test")
    service.group_item_directories(db_session, run=run)

    oversized = db_session.query(MediaRecoveryItemGroup).filter_by(run_id=run.id, recovery_item_id="SP-20260708-0009").one()
    review = db_session.query(MediaRecoveryItemGroup).filter_by(run_id=run.id, recovery_item_id="RECOVERY-REVIEW-0001").one()
    assert oversized.grouping_status == "needs_grouping_review"
    assert review.grouping_status == "needs_grouping_review"
    assert len(review.media_paths_json) == 1
