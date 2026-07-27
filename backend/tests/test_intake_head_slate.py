from __future__ import annotations

import base64
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image, ImageFilter

from app.models.enums import ListingStatus
from app.models.models import (
    CanonicalItem,
    CanonicalItemFact,
    IntakeNotification,
    IntakePhoto,
    IntakePhotoBatch,
    IntakeProviderMedia,
    IntakeReconciliationEvent,
    IntakeReconciliationJob,
    IntakeSourceState,
    IntakeSlate,
    Listing,
    SlateObservation,
    User,
)
from app.services.alert_service import AlertService
from app.services.google_photos import GooglePhotoEnumeration, GooglePhotosService
from app.services.intake_slate import IntakeSlateService


def _make_image_file(name: str, color: str = 'white') -> str:
    handle = tempfile.NamedTemporaryFile(prefix=name, suffix='.jpg', delete=False)
    handle.close()
    Image.new('RGB', (40, 40), color=color).save(handle.name, format='JPEG')
    return handle.name


def _write_qr_image(data_url: str) -> str:
    encoded = data_url.split(',', 1)[1]
    payload = base64.b64decode(encoded)
    handle = tempfile.NamedTemporaryFile(prefix='posterpro-slate-', suffix='.png', delete=False)
    handle.write(payload)
    handle.flush()
    handle.close()
    return handle.name


def _create_user(db_session, email='intake@example.com'):
    user = User(email=email)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_head_slate_qr_payload_round_trip(db_session):
    user = _create_user(db_session)
    service = IntakeSlateService()

    slate, qr_payload, qr_data_url = service.create_slate(
        db_session,
        user=user,
        payload={
            'session_id': '2026-07-05-STORAGE-A',
            'location': 'A-01',
            'title': 'Ryobi 40V charger',
            'brand': 'Ryobi',
            'condition': 'Used',
        },
    )

    image_path = _write_qr_image(qr_data_url)
    decoded = service.decode_slate_payload(image_path)

    assert decoded is not None
    assert decoded['type'] == 'posterpro_head_slate'
    assert decoded['item_id'] == slate.item_id
    assert decoded['session_id'] == '2026-07-05-STORAGE-A'
    assert decoded['title'] == 'Ryobi 40V charger'
    assert qr_payload['created_at']


def test_head_slate_qr_payload_round_trip_from_photographed_style_image(db_session):
    user = _create_user(db_session, email='photographed-slate@example.com')
    service = IntakeSlateService()

    slate, _, qr_data_url = service.create_slate(
        db_session,
        user=user,
        payload={
            'session_id': '2026-07-05-STORAGE-B',
            'location': 'B-01',
            'title': 'Laptop charger',
        },
    )

    image_path = _write_qr_image(qr_data_url)
    transformed_path = tempfile.NamedTemporaryFile(prefix='posterpro-slate-photo-', suffix='.jpg', delete=False).name
    with Image.open(image_path).convert('RGB') as qr_image:
        canvas = Image.new('RGB', (1600, 1200), 'white')
        warped = qr_image.resize((900, 900)).rotate(4.5, expand=True, fillcolor='white').filter(ImageFilter.GaussianBlur(radius=0.5))
        canvas.paste(warped, (320, 120))
        photographed = canvas.resize((1100, 825)).filter(ImageFilter.GaussianBlur(radius=0.7))
        photographed.save(transformed_path, format='JPEG', quality=88)

    decoded = service.decode_slate_payload(transformed_path)

    assert decoded is not None
    assert decoded['item_id'] == slate.item_id
    assert decoded['session_id'] == '2026-07-05-STORAGE-B'



def test_rebuild_batches_groups_everything_after_a_slate_until_the_next_slate(db_session):
    user = _create_user(db_session, email='grouping@example.com')
    service = IntakeSlateService()

    slate_one, qr_one, _ = service.create_slate(db_session, user=user, payload={'session_id': 'SESSION-1', 'title': 'Item one'})
    slate_two, qr_two, _ = service.create_slate(db_session, user=user, payload={'session_id': 'SESSION-1', 'title': 'Item two'})

    start = datetime.now(UTC)
    photos = [
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='slate-1', local_path=_make_image_file('slate1'), imported_at=start, metadata_json={'qr_payload': qr_one}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='photo-1', local_path=_make_image_file('photo1', 'red'), imported_at=start + timedelta(seconds=1), metadata_json={}, is_slate=False, is_public_listing_candidate=True),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='photo-2', local_path=_make_image_file('photo2', 'blue'), imported_at=start + timedelta(seconds=2), metadata_json={}, is_slate=False, is_public_listing_candidate=True),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='slate-2', local_path=_make_image_file('slate2'), imported_at=start + timedelta(seconds=3), metadata_json={'qr_payload': qr_two}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='photo-3', local_path=_make_image_file('photo3', 'green'), imported_at=start + timedelta(seconds=4), metadata_json={}, is_slate=False, is_public_listing_candidate=True),
    ]
    db_session.add_all(photos)
    db_session.commit()

    result = service.rebuild_batches_for_user(db_session, user_id=user.id)

    rows = db_session.query(IntakePhoto).order_by(IntakePhoto.imported_at.asc(), IntakePhoto.id.asc()).all()
    assert result['assigned_photos'] == 3
    assert rows[0].item_id == slate_one.item_id
    assert rows[1].item_id == slate_one.item_id
    assert rows[2].item_id == slate_one.item_id
    assert rows[3].item_id == slate_two.item_id
    assert rows[4].item_id == slate_two.item_id
    assert rows[0].is_public_listing_candidate is False
    assert rows[3].is_internal_only is True



def test_rebuild_batches_leaves_photos_before_first_slate_unassigned(db_session):
    user = _create_user(db_session, email='unassigned@example.com')
    service = IntakeSlateService()
    slate, qr_payload, _ = service.create_slate(db_session, user=user, payload={'session_id': 'SESSION-2', 'title': 'Only item'})
    start = datetime.now(UTC)

    leading = IntakePhoto(
        user_id=user.id,
        source_provider='google_photos',
        source_photo_id='leading-photo',
        local_path=_make_image_file('leading', 'yellow'),
        imported_at=start,
        metadata_json={},
        is_slate=False,
        is_public_listing_candidate=True,
    )
    slate_photo = IntakePhoto(
        user_id=user.id,
        source_provider='google_photos',
        source_photo_id='slate-photo',
        local_path=_make_image_file('slate-leading'),
        imported_at=start + timedelta(seconds=1),
        metadata_json={'qr_payload': qr_payload},
        is_slate=True,
        is_internal_only=True,
        is_public_listing_candidate=False,
    )
    product = IntakePhoto(
        user_id=user.id,
        source_provider='google_photos',
        source_photo_id='product-photo',
        local_path=_make_image_file('product', 'orange'),
        imported_at=start + timedelta(seconds=2),
        metadata_json={},
        is_slate=False,
        is_public_listing_candidate=True,
    )
    db_session.add_all([leading, slate_photo, product])
    db_session.commit()

    service.rebuild_batches_for_user(db_session, user_id=user.id)
    db_session.refresh(leading)
    db_session.refresh(product)

    assert leading.batch_id is None
    assert leading.item_id is None
    assert product.item_id == slate.item_id
    assert product.batch_id is not None


def test_tail_slate_recovers_preceding_unassigned_photos_into_the_same_batch(db_session):
    user = _create_user(db_session, email='tail-slate@example.com')
    service = IntakeSlateService()
    slate, qr_payload, _ = service.create_slate(
        db_session,
        user=user,
        payload={'session_id': 'SESSION-TAIL', 'title': 'Tail slate item', 'boundary_position': 'tail'},
    )
    start = datetime.now(UTC)
    before_one = IntakePhoto(
        user_id=user.id,
        source_provider='google_photos',
        source_photo_id='tail-before-1',
        local_path=_make_image_file('tail-before-1', 'yellow'),
        imported_at=start,
        metadata_json={},
        is_slate=False,
        is_public_listing_candidate=True,
    )
    before_two = IntakePhoto(
        user_id=user.id,
        source_provider='google_photos',
        source_photo_id='tail-before-2',
        local_path=_make_image_file('tail-before-2', 'orange'),
        imported_at=start + timedelta(seconds=1),
        metadata_json={},
        is_slate=False,
        is_public_listing_candidate=True,
    )
    slate_photo = IntakePhoto(
        user_id=user.id,
        source_provider='google_photos',
        source_photo_id='tail-slate-photo',
        local_path=_make_image_file('tail-slate'),
        imported_at=start + timedelta(seconds=2),
        metadata_json={'qr_payload': qr_payload},
        is_slate=True,
        is_internal_only=True,
        is_public_listing_candidate=False,
    )
    db_session.add_all([before_one, before_two, slate_photo])
    db_session.commit()

    result = service.rebuild_batches_for_user(db_session, user_id=user.id)
    rows = db_session.query(IntakePhoto).order_by(IntakePhoto.imported_at.asc(), IntakePhoto.id.asc()).all()
    batch = db_session.query(IntakePhotoBatch).filter(IntakePhotoBatch.item_id == slate.item_id).one()

    assert result['assigned_photos'] == 2
    assert rows[0].item_id == slate.item_id
    assert rows[1].item_id == slate.item_id
    assert rows[2].item_id == slate.item_id
    assert batch.metadata_json['tail_boundary_used'] is True


def test_recover_existing_slates_promotes_previously_unassigned_qr_photo(db_session):
    user = _create_user(db_session, email='recover-existing-slate@example.com')
    service = IntakeSlateService()
    slate, _, qr_data_url = service.create_slate(
        db_session,
        user=user,
        payload={'session_id': 'SESSION-RECOVER', 'title': 'Recover me'},
    )
    image_path = _write_qr_image(qr_data_url)
    photo = IntakePhoto(
        user_id=user.id,
        source_provider='google_photos',
        source_photo_id='recover-slate-photo',
        local_path=image_path,
        metadata_json={},
        is_slate=False,
        is_public_listing_candidate=True,
    )
    db_session.add(photo)
    db_session.commit()

    recovered = service.recover_existing_slates(db_session, user=user)
    db_session.refresh(photo)
    db_session.refresh(slate)

    assert recovered == 1
    assert photo.is_slate is True
    assert photo.item_id == slate.item_id
    assert slate.slate_image_id == photo.id



def test_create_draft_from_batch_uses_item_id_as_inventory_and_excludes_slate_images(db_session, monkeypatch):
    user = _create_user(db_session, email='drafts@example.com')
    service = IntakeSlateService()
    slate, qr_payload, _ = service.create_slate(
        db_session,
        user=user,
        payload={
            'session_id': 'SESSION-3',
            'title': 'Ryobi charger',
            'brand': 'Ryobi',
            'condition': 'Used',
            'box_id': 'BX-0007',
            'location': 'A-02',
        },
    )
    start = datetime.now(UTC)
    db_session.add_all([
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='s3', local_path=_make_image_file('draft-slate'), imported_at=start, metadata_json={'qr_payload': qr_payload}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='p3', local_path=_make_image_file('draft-product', 'purple'), imported_at=start + timedelta(seconds=1), metadata_json={}, is_slate=False, is_public_listing_candidate=True),
    ])
    db_session.commit()
    service.rebuild_batches_for_user(db_session, user_id=user.id)

    monkeypatch.setattr(service.ai, 'generate', lambda _signals: {
        'title': 'Ryobi 40V charger',
        'description': 'Clean tested charger ready for use.',
        'category_suggestion': 'Chargers',
        'condition': 'Used',
        'item_specifics': {'Brand': 'Ryobi', 'Model': '40V'},
        'tags': ['ryobi', 'charger'],
        'estimated_value': 39.99,
        'missing_information': [],
        'photo_notes': [],
        'research_queries': [],
        'draft_quality': 'strong',
        'generation_source': 'test',
        'model_used': 'test',
    })
    monkeypatch.setattr(service.ebay, 'enrich_price', lambda *_args, **_kwargs: {'comparables': []})
    monkeypatch.setattr(service.pricing, 'recommend_price', lambda *_args, **_kwargs: {'suggested_price': 39.99})

    created = service.create_drafts_for_ready_batches(db_session, user_id=user.id)

    batch = db_session.query(IntakePhotoBatch).filter(IntakePhotoBatch.item_id == slate.item_id).one()
    listing = db_session.get(Listing, batch.draft_listing_id)

    assert created == 1
    assert listing is not None
    assert listing.status == ListingStatus.draft
    assert listing.source_type == 'intake_head_slate'
    assert listing.source_metadata['intake']['item_id'] == slate.item_id
    assert listing.source_metadata['intake']['box_id'] == 'BX-0007'
    assert len(listing.image_urls or []) == 1
    assert all(not image.get('is_reference') for image in (listing.listing_images or []))
    assert slate.listing_id == listing.id


def test_capture_time_not_arrival_time_controls_late_photo_assignment(db_session):
    user = _create_user(db_session, email='capture-order@example.com')
    service = IntakeSlateService()
    first, first_payload, _ = service.create_slate(db_session, user=user, payload={'session_id': 'TIMELINE', 'title': 'First'})
    second, second_payload, _ = service.create_slate(db_session, user=user, payload={'session_id': 'TIMELINE', 'title': 'Second'})
    start = datetime.now(UTC)
    rows = [
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='timeline-s1', local_path=_make_image_file('timeline-s1'), captured_at=start, imported_at=start + timedelta(hours=2), metadata_json={'qr_payload': first_payload}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='timeline-s2', local_path=_make_image_file('timeline-s2'), captured_at=start + timedelta(minutes=10), imported_at=start + timedelta(minutes=11), metadata_json={'qr_payload': second_payload}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
        # This photo arrives last but was captured before the second slate.
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='timeline-late', local_path=_make_image_file('timeline-late', 'red'), captured_at=start + timedelta(minutes=5), imported_at=start + timedelta(hours=3), metadata_json={}, is_public_listing_candidate=True),
    ]
    db_session.add_all(rows)
    db_session.commit()

    service.rebuild_batches_for_user(db_session, user_id=user.id)
    db_session.refresh(rows[2])

    assert rows[2].item_id == first.item_id
    assert rows[2].item_id != second.item_id


def test_replacement_slate_updates_existing_canonical_item_without_duplicate_listing(db_session):
    user = _create_user(db_session, email='versioned-slate@example.com')
    service = IntakeSlateService()
    slate, payload, _ = service.create_slate(
        db_session,
        user=user,
        payload={'session_id': 'VERSIONED', 'item_id': 'ITEM-00482', 'box_id': 'B12', 'location': 'A-01'},
    )
    start = datetime.now(UTC)
    original_photo = IntakePhoto(
        user_id=user.id, source_provider='google_photos', source_photo_id='versioned-original', local_path=_make_image_file('versioned-original'),
        captured_at=start, metadata_json={'qr_payload': payload}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False,
    )
    db_session.add(original_photo)
    db_session.commit()
    service._upsert_slate_from_qr(db_session, user=user, qr_payload=payload, photo=original_photo)
    replacement = {**payload, 'box_id': 'C07', 'condition': 'Open box, missing mounting screws', 'created_at': (start + timedelta(days=1)).isoformat()}
    replacement_photo = IntakePhoto(
        user_id=user.id, source_provider='google_photos', source_photo_id='versioned-replacement', local_path=_make_image_file('versioned-replacement'),
        captured_at=start + timedelta(days=1), metadata_json={}, is_slate=False,
    )
    db_session.add(replacement_photo)
    db_session.commit()
    service._upsert_slate_from_qr(db_session, user=user, qr_payload=replacement, photo=replacement_photo)
    db_session.commit()

    items = db_session.query(CanonicalItem).filter(CanonicalItem.user_id == user.id, CanonicalItem.item_id == 'ITEM-00482').all()
    observations = db_session.query(SlateObservation).filter(SlateObservation.item_id == 'ITEM-00482').all()
    current_box = db_session.query(CanonicalItemFact).filter(CanonicalItemFact.canonical_item_id == items[0].id, CanonicalItemFact.field_name == 'box_id', CanonicalItemFact.is_current.is_(True)).one()

    assert len(items) == 1
    # The generated web slate is retained as operator evidence in addition to
    # the two photographed slate observations; it must still resolve to one item.
    assert len([row for row in observations if row.media_id is not None]) == 2
    assert slate.box_id == 'C07'
    assert current_box.value_json == 'C07'


def test_google_photos_parser_keeps_shared_album_pw_urls_and_count_metadata():
    service = GooglePhotosService()
    html = '''
    <html><body>
    ["AF1QipAlpha123",["https://lh3.googleusercontent.com/pw/AP1GczExampleAlpha=w2048-h1536-no?authuser=0",3072,4096],1783288965307]
    ["AF1QipBeta456",["https://lh3.googleusercontent.com/pw/AP1GczExampleBeta",2048,2048],1783288999000]
    ,"S1NyUWxtY2pHZ1ZEc3FxRm9Rc2Y2XzA2d3VRVy13",1,520,
    </body></html>
    '''

    entries = service.extract_photo_entries_from_html(html)
    visible_count = service.extract_album_visible_count_from_html(html)

    assert len(entries) == 2
    assert entries[0]['source_photo_id'] == 'AF1QipAlpha123'
    assert entries[0]['url'].startswith('https://lh3.googleusercontent.com/pw/AP1GczExampleAlpha')
    assert entries[0]['captured_at'] == '2026-07-05T22:02:45.307000+00:00'
    assert entries[1]['source_photo_id'] == 'AF1QipBeta456'
    assert entries[1]['captured_at'] == '2026-07-05T22:03:19+00:00'
    assert visible_count == 520


def test_google_photos_parser_merges_playwright_continuation_when_visible_count_exceeds_html(monkeypatch):
    service = GooglePhotosService()
    html = '''
    <html><body>
    ["AF1QipAlpha123",["https://lh3.googleusercontent.com/pw/AP1GczExampleAlpha=w2048-h1536-no",3072,4096],1783288965307]
    ,"S1NyUWxtY2pHZ1ZEc3FxRm9Rc2Y2XzA2d3VRVy13",1,3,
    </body></html>
    '''
    monkeypatch.setattr(httpx, 'Client', lambda *args, **kwargs: _FakeHttpxClient(html))
    monkeypatch.setattr(
        service,
        'extract_photo_entries_via_playwright',
        lambda _album_url: [
            {
                'url': 'https://lh3.googleusercontent.com/pw/AP1GczExampleAlpha',
                'source_photo_id': 'AF1QipAlpha123',
                'suggested_basename': 'google-photo-AF1QipAlpha123',
                'original_filename': 'google-photo-AF1QipAlpha123.jpg',
                'source_order': 0,
                'captured_at': '2026-07-05T22:02:45.307000+00:00',
                'uploaded_at': None,
            },
            {
                'url': 'https://lh3.googleusercontent.com/pw/AP1GczExampleBeta',
                'source_photo_id': 'AF1QipBeta456',
                'suggested_basename': 'google-photo-AF1QipBeta456',
                'original_filename': 'google-photo-AF1QipBeta456.jpg',
                'source_order': 1,
                'captured_at': None,
                'uploaded_at': None,
            },
            {
                'url': 'https://lh3.googleusercontent.com/pw/AP1GczExampleGamma',
                'source_photo_id': 'AF1QipGamma789',
                'suggested_basename': 'google-photo-AF1QipGamma789',
                'original_filename': 'google-photo-AF1QipGamma789.jpg',
                'source_order': 2,
                'captured_at': None,
                'uploaded_at': None,
            },
        ],
    )

    entries = service.extract_photo_entries('https://photos.app.goo.gl/example')

    assert [entry['source_photo_id'] for entry in entries] == ['AF1QipAlpha123', 'AF1QipBeta456', 'AF1QipGamma789']
    assert entries[0]['captured_at'] == '2026-07-05T22:02:45.307000+00:00'


def test_google_photos_playwright_tile_entry_extracts_source_photo_id_and_timestamp():
    service = GooglePhotosService()

    entry = service._entry_from_playwright_tile(
        {
            'href': './share/AF1QipAlbum123/photo/AF1QipPhoto456?key=abc123',
            'aria': 'Photo - Portrait - Jul 7, 2026, 8:00:22 PM',
            'preview_url': 'https://lh3.googleusercontent.com/pw/AP1GczExampleTile=w226-h301-no',
        },
        source_order=12,
    )

    assert entry is not None
    assert entry['source_photo_id'] == 'AF1QipPhoto456'
    assert entry['url'] == 'https://lh3.googleusercontent.com/pw/AP1GczExampleTile'
    assert entry['captured_at'] == '2026-07-07T20:00:22+00:00'
    assert entry['source_order'] == 12


class _FakeHttpxResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeHttpxClient:
    def __init__(self, text: str):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, _url: str):
        return _FakeHttpxResponse(self.text)


def test_coerce_slate_text_builds_payload_without_qr():
    service = IntakeSlateService()

    payload = service._coerce_slate_text(
        '\n'.join(
            [
                'POSTERPRO HEAD SLATE',
                'SESSION: 2026-07-08-INTAKE',
                'ITEM: SP-20260708-0007',
                'BOX: BX-0007',
                'LOC: A-01',
                'TITLE: Laptop charger',
                'DATE: 2026-07-08T14:30:00+00:00',
            ]
        )
    )

    assert payload is not None
    assert payload['type'] == 'posterpro_head_slate'
    assert payload['session_id'] == '2026-07-08-INTAKE'
    assert payload['item_id'] == 'SP-20260708-0007'
    assert payload['box_id'] == 'BX-0007'
    assert payload['location'] == 'A-01'
    assert payload['title'] == 'Laptop charger'



def test_export_csv_includes_intake_identifiers(db_session, monkeypatch):
    user = _create_user(db_session, email='export@example.com')
    service = IntakeSlateService()
    slate, qr_payload, _ = service.create_slate(
        db_session,
        user=user,
        payload={'session_id': 'SESSION-4', 'title': 'Export item', 'box_id': 'BX-0011', 'location': 'SHELF-9'},
    )
    start = datetime.now(UTC)
    db_session.add_all([
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='s4', local_path=_make_image_file('csv-slate'), imported_at=start, metadata_json={'qr_payload': qr_payload}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='p4', local_path=_make_image_file('csv-product', 'pink'), imported_at=start + timedelta(seconds=1), metadata_json={}, is_slate=False, is_public_listing_candidate=True),
    ])
    db_session.commit()
    service.rebuild_batches_for_user(db_session, user_id=user.id)
    monkeypatch.setattr(service.ai, 'generate', lambda _signals: {
        'title': 'Export item',
        'description': 'Export description',
        'category_suggestion': 'General',
        'condition': 'Used',
        'item_specifics': {},
        'tags': [],
        'estimated_value': 20.0,
        'missing_information': [],
        'photo_notes': [],
        'research_queries': [],
        'draft_quality': 'strong',
        'generation_source': 'test',
        'model_used': 'test',
    })
    monkeypatch.setattr(service.ebay, 'enrich_price', lambda *_args, **_kwargs: {'comparables': []})
    monkeypatch.setattr(service.pricing, 'recommend_price', lambda *_args, **_kwargs: {'suggested_price': 20.0})
    service.create_drafts_for_ready_batches(db_session, user_id=user.id)

    csv_payload = service.export_csv(db_session, user_id=user.id)

    assert 'Item ID,Box ID,Location,Title' in csv_payload
    assert slate.item_id in csv_payload
    assert 'BX-0011' in csv_payload
    assert 'SHELF-9' in csv_payload


def test_manual_assignment_can_promote_unassigned_photos_into_a_real_batch_and_draft(db_session, monkeypatch):
    user = _create_user(db_session, email='manual-assign@example.com')
    service = IntakeSlateService()
    slate, _, _ = service.create_slate(
        db_session,
        user=user,
        payload={'session_id': 'SESSION-5', 'title': 'Manual grouping item', 'box_id': 'BX-0099', 'location': 'BENCH-1'},
    )
    start = datetime.now(UTC)
    photos = [
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='manual-1', local_path=_make_image_file('manual-1', 'red'), imported_at=start, metadata_json={}, is_slate=False, is_public_listing_candidate=True),
        IntakePhoto(user_id=user.id, source_provider='google_photos', source_photo_id='manual-2', local_path=_make_image_file('manual-2', 'blue'), imported_at=start + timedelta(seconds=1), metadata_json={}, is_slate=False, is_public_listing_candidate=True),
    ]
    db_session.add_all(photos)
    db_session.commit()
    monkeypatch.setattr(service.ai, 'generate', lambda _signals: {
        'title': 'Manual grouping item',
        'description': 'Generated from manual assignment',
        'category_suggestion': 'General',
        'condition': 'Used',
        'item_specifics': {},
        'tags': ['manual'],
        'estimated_value': 18.0,
        'missing_information': [],
        'photo_notes': [],
        'research_queries': [],
        'draft_quality': 'strong',
        'generation_source': 'test',
        'model_used': 'test',
    })
    monkeypatch.setattr(service.ebay, 'enrich_price', lambda *_args, **_kwargs: {'comparables': []})
    monkeypatch.setattr(service.pricing, 'recommend_price', lambda *_args, **_kwargs: {'suggested_price': 18.0})

    batch = service.assign_unassigned_photos_to_item(db_session, user_id=user.id, item_id=slate.item_id)

    assert batch.item_id == slate.item_id
    assert batch.draft_listing_id is not None
    listing = db_session.get(Listing, batch.draft_listing_id)
    assert listing is not None
    assert listing.source_metadata['intake']['item_id'] == slate.item_id
    assigned_rows = db_session.query(IntakePhoto).filter(IntakePhoto.batch_id == batch.id).all()
    assert len(assigned_rows) == 2
    assert all((row.metadata_json or {}).get('manual_item_id') == slate.item_id for row in assigned_rows)


def test_alerts_include_intake_review_notifications(db_session):
    user = _create_user(db_session, email='intake-alerts@example.com')
    user.settings_json = {
        'intake_settings': {
            'enabled': True,
            'album_url': 'https://photos.app.goo.gl/example',
        }
    }
    db_session.add(user)
    db_session.commit()

    slate = IntakeSlate(user_id=user.id, item_id='SP-20260706-0007', session_id='SESSION-6', status='captured')
    db_session.add(slate)
    db_session.flush()
    batch = IntakePhotoBatch(user_id=user.id, item_id=slate.item_id, slate_id=slate.id, status='drafted', draft_listing_id=123)
    db_session.add(batch)
    db_session.add(
        IntakePhoto(
            user_id=user.id,
            source_provider='google_photos',
            source_photo_id='alert-unassigned',
            local_path=_make_image_file('alert-unassigned', 'green'),
            metadata_json={},
            is_slate=False,
            is_public_listing_candidate=True,
        )
    )
    db_session.commit()

    alerts = AlertService().generate_alerts(db_session, user.id)
    types = {item.get('type') for item in alerts}
    assert 'intake_unassigned_photos' in types
    assert 'intake_review_ready' in types


def test_alerts_consider_drive_link_enabled_intake_source(db_session):
    user = _create_user(db_session, email='intake-alerts-drive@example.com')
    user.settings_json = {
        'intake_settings': {
            'enabled': True,
            'folder_id': 'https://drive.google.com/drive/folders/example-drive',
        }
    }
    db_session.add(user)
    db_session.commit()
    db_session.add(
        IntakePhoto(
            user_id=user.id,
            source_provider='google_photos',
            source_photo_id='drive-unassigned',
            local_path=_make_image_file('drive-unassigned', 'green'),
            metadata_json={},
            is_slate=False,
            is_public_listing_candidate=True,
        )
    )
    db_session.commit()

    alerts = AlertService().generate_alerts(db_session, user.id)

    assert any(item.get('type') == 'intake_unassigned_photos' for item in alerts)


def test_monitor_uses_drive_link_fallback_when_album_url_missing(db_session, monkeypatch):
    user = _create_user(db_session, email='drive-link@example.com')
    service = IntakeSlateService()
    service.save_settings(
        db=db_session,
        user=user,
        payload={
            'enabled': True,
            'album_url': '',
            'folder_id': 'https://drive.google.com/drive/folders/drive-link-example',
            'auto_draft_listing': False,
        },
    )

    image_path = _make_image_file('drive-monitor', 'cyan')
    entries = [{
            'url': 'https://lh3.googleusercontent.com/drive-photo',
            'source_photo_id': 'drive-photo-1',
            'suggested_basename': 'drive-photo-1',
            'original_filename': 'drive-photo-1.jpg',
            'source_order': 0,
            'captured_at': None,
            'uploaded_at': None,
        }]
    monkeypatch.setattr(
        service.google_photos,
        'enumerate_photo_entries',
        lambda _source_url: GooglePhotoEnumeration(entries, enumeration_complete=True),
    )
    monkeypatch.setattr(service.storage, 'save_from_url', lambda *_args, **_kwargs: image_path)

    result = service.monitor_google_album(db_session, user=user)

    assert result['imported'] == 1
    assert result['slates_detected'] == 0
    assert db_session.query(IntakePhoto).count() == 1
    saved_settings = service.settings_for_user(user)
    assert saved_settings['folder_id'] == 'https://drive.google.com/drive/folders/drive-link-example'


def _monitor_listing_ai(monkeypatch, service):
    monkeypatch.setattr(service.ai, 'generate', lambda signals: {
        'title': signals.get('title_hint') or 'Intake item',
        'description': 'Generated intake draft',
        'category_suggestion': 'General',
        'condition': 'Used',
        'item_specifics': {},
        'tags': [],
        'estimated_value': 22.0,
        'missing_information': [],
        'photo_notes': [],
        'research_queries': [],
        'draft_quality': 'strong',
        'generation_source': 'test',
        'model_used': 'test',
    })
    monkeypatch.setattr(service.ebay, 'enrich_price', lambda *_args, **_kwargs: {'comparables': []})
    monkeypatch.setattr(service.pricing, 'recommend_price', lambda *_args, **_kwargs: {'recommended_price': 22.0})


def test_monitor_late_photo_reconciles_existing_draft_by_capture_time(db_session, monkeypatch):
    user = _create_user(db_session, email='monitor-late-photo@example.com')
    service = IntakeSlateService()
    service.save_settings(
        db=db_session,
        user=user,
        payload={
            'enabled': True,
            'album_url': 'https://photos.app.goo.gl/monitor-late-photo',
            'auto_draft_listing': True,
            'auto_draft_when_provisional': True,
            'quiet_period_seconds': 0,
        },
    )
    slate_a, qr_a, _ = service.create_slate(db_session, user=user, payload={'session_id': 'MONITOR', 'title': 'Item A'})
    slate_b, qr_b, _ = service.create_slate(db_session, user=user, payload={'session_id': 'MONITOR', 'title': 'Item B'})
    _monitor_listing_ai(monkeypatch, service)
    start = datetime.now(UTC) - timedelta(hours=1)
    files = {key: _make_image_file(f'monitor-{key}', color) for key, color in {
        'slate-a': 'white', 'a-1': 'red', 'a-2': 'blue', 'slate-b': 'white', 'b-1': 'green', 'a-late': 'orange',
    }.items()}
    payloads = {files['slate-a']: qr_a, files['slate-b']: qr_b}
    entries = [
        {'url': 'slate-a', 'source_photo_id': 'slate-a', 'suggested_basename': 'slate-a', 'original_filename': '001-slate-a.jpg', 'source_order': 1, 'captured_at': (start + timedelta(seconds=1)).isoformat(), 'uploaded_at': (start + timedelta(minutes=5)).isoformat()},
        {'url': 'a-1', 'source_photo_id': 'a-1', 'suggested_basename': 'a-1', 'original_filename': '002-a-1.jpg', 'source_order': 2, 'captured_at': (start + timedelta(seconds=2)).isoformat(), 'uploaded_at': (start + timedelta(minutes=5)).isoformat()},
        {'url': 'a-2', 'source_photo_id': 'a-2', 'suggested_basename': 'a-2', 'original_filename': '004-a-2.jpg', 'source_order': 4, 'captured_at': (start + timedelta(seconds=4)).isoformat(), 'uploaded_at': (start + timedelta(minutes=5)).isoformat()},
        {'url': 'slate-b', 'source_photo_id': 'slate-b', 'suggested_basename': 'slate-b', 'original_filename': '005-slate-b.jpg', 'source_order': 5, 'captured_at': (start + timedelta(seconds=5)).isoformat(), 'uploaded_at': (start + timedelta(minutes=5)).isoformat()},
        {'url': 'b-1', 'source_photo_id': 'b-1', 'suggested_basename': 'b-1', 'original_filename': '006-b-1.jpg', 'source_order': 6, 'captured_at': (start + timedelta(seconds=6)).isoformat(), 'uploaded_at': (start + timedelta(minutes=5)).isoformat()},
    ]
    monkeypatch.setattr(
        service.google_photos,
        'enumerate_photo_entries',
        lambda _url: GooglePhotoEnumeration(entries, enumeration_complete=True),
    )
    monkeypatch.setattr(service.storage, 'save_from_url', lambda url, **_kwargs: files[url])
    monkeypatch.setattr(service, 'classify_photo_for_intake', lambda _path: {'is_qr_candidate': True, 'is_probable_slate': False})
    monkeypatch.setattr(service, 'decode_slate_payload_isolated', lambda path, **_kwargs: payloads.get(path))

    first = service.monitor_google_album(db_session, user=user)
    batch_a = db_session.query(IntakePhotoBatch).filter_by(user_id=user.id, item_id=slate_a.item_id).one()
    assert first['imported'] == 5
    assert batch_a.draft_listing_id is not None
    listing_id = batch_a.draft_listing_id

    entries.append({
        'url': 'a-late', 'source_photo_id': 'a-late', 'suggested_basename': 'a-late', 'original_filename': '003-a-late.jpg',
        'source_order': 3, 'captured_at': (start + timedelta(seconds=3)).isoformat(), 'uploaded_at': datetime.now(UTC).isoformat(),
    })
    second = service.monitor_google_album(db_session, user=user)
    late_photo = db_session.query(IntakePhoto).filter_by(user_id=user.id, source_photo_id='a-late').one()
    db_session.refresh(batch_a)
    listing = db_session.get(Listing, listing_id)

    assert second['imported'] == 1
    assert late_photo.item_id == slate_a.item_id
    assert late_photo.batch_id == batch_a.id
    assert batch_a.draft_listing_id == listing_id
    assert late_photo.id in listing.source_metadata['intake']['photo_ids']
    assert db_session.query(CanonicalItem).filter_by(user_id=user.id, item_id=slate_a.item_id).count() == 1
    canonical_a = db_session.query(CanonicalItem).filter_by(user_id=user.id, item_id=slate_a.item_id).one()
    assert db_session.query(IntakeNotification).filter_by(
        user_id=user.id,
        notification_type='late_photo_added',
        canonical_item_id=canonical_a.id,
    ).count() == 1
    assert db_session.query(IntakeReconciliationJob).filter_by(user_id=user.id, status='completed').count() >= 1


def test_monitor_late_slate_splits_existing_group_without_duplicate_items(db_session, monkeypatch):
    user = _create_user(db_session, email='monitor-late-slate@example.com')
    service = IntakeSlateService()
    service.save_settings(db=db_session, user=user, payload={'enabled': True, 'album_url': 'https://photos.app.goo.gl/monitor-late-slate', 'auto_draft_listing': False})
    slate_a, qr_a, _ = service.create_slate(db_session, user=user, payload={'session_id': 'SPLIT', 'title': 'Item A'})
    slate_b, qr_b, _ = service.create_slate(db_session, user=user, payload={'session_id': 'SPLIT', 'title': 'Item B'})
    slate_c, qr_c, _ = service.create_slate(db_session, user=user, payload={'session_id': 'SPLIT', 'title': 'Item C'})
    start = datetime.now(UTC) - timedelta(hours=1)
    files = {key: _make_image_file(f'split-{key}', color) for key, color in {
        'slate-a': 'white', 'a-1': 'red', 'b-1': 'blue', 'slate-c': 'white', 'c-1': 'green', 'slate-b': 'white',
    }.items()}
    payloads = {files['slate-a']: qr_a, files['slate-b']: qr_b, files['slate-c']: qr_c}
    entries = [
        {'url': 'slate-a', 'source_photo_id': 'split-slate-a', 'suggested_basename': 'a', 'original_filename': '001-a.jpg', 'source_order': 1, 'captured_at': (start + timedelta(seconds=1)).isoformat()},
        {'url': 'a-1', 'source_photo_id': 'split-a-1', 'suggested_basename': 'a1', 'original_filename': '002-a.jpg', 'source_order': 2, 'captured_at': (start + timedelta(seconds=2)).isoformat()},
        {'url': 'b-1', 'source_photo_id': 'split-b-1', 'suggested_basename': 'b1', 'original_filename': '004-b.jpg', 'source_order': 4, 'captured_at': (start + timedelta(seconds=4)).isoformat()},
        {'url': 'slate-c', 'source_photo_id': 'split-slate-c', 'suggested_basename': 'c', 'original_filename': '005-c.jpg', 'source_order': 5, 'captured_at': (start + timedelta(seconds=5)).isoformat()},
        {'url': 'c-1', 'source_photo_id': 'split-c-1', 'suggested_basename': 'c1', 'original_filename': '006-c.jpg', 'source_order': 6, 'captured_at': (start + timedelta(seconds=6)).isoformat()},
    ]
    monkeypatch.setattr(
        service.google_photos,
        'enumerate_photo_entries',
        lambda _url: GooglePhotoEnumeration(entries, enumeration_complete=True),
    )
    monkeypatch.setattr(service.storage, 'save_from_url', lambda url, **_kwargs: files[url])
    monkeypatch.setattr(service, 'classify_photo_for_intake', lambda _path: {'is_qr_candidate': True, 'is_probable_slate': False})
    monkeypatch.setattr(service, 'decode_slate_payload_isolated', lambda path, **_kwargs: payloads.get(path))

    service.monitor_google_album(db_session, user=user)
    middle = db_session.query(IntakePhoto).filter_by(user_id=user.id, source_photo_id='split-b-1').one()
    assert middle.item_id == slate_a.item_id

    entries.append({'url': 'slate-b', 'source_photo_id': 'split-slate-b', 'suggested_basename': 'b', 'original_filename': '003-b.jpg', 'source_order': 3, 'captured_at': (start + timedelta(seconds=3)).isoformat()})
    service.monitor_google_album(db_session, user=user)
    db_session.refresh(middle)

    assert middle.item_id == slate_b.item_id
    assert db_session.query(CanonicalItem).filter_by(user_id=user.id, item_id=slate_b.item_id).count() == 1
    assert db_session.query(SlateObservation).filter_by(user_id=user.id, item_id=slate_b.item_id).count() >= 1


def test_reconciliation_job_idempotency_and_expired_lease_recovery(db_session):
    user = _create_user(db_session, email='job-lease@example.com')
    service = IntakeSlateService()
    slate, qr_payload, _ = service.create_slate(db_session, user=user, payload={'session_id': 'LEASE', 'title': 'Lease item'})
    photo = IntakePhoto(
        user_id=user.id,
        source_provider='manual_upload',
        source_photo_id='lease-photo',
        local_path=_make_image_file('lease-photo', 'purple'),
        captured_at=datetime.now(UTC) - timedelta(minutes=2),
        imported_at=datetime.now(UTC),
        metadata_json={'source_fingerprint': 'lease-v1', 'qr_payload': qr_payload},
        is_slate=True,
        is_internal_only=True,
        is_public_listing_candidate=False,
    )
    db_session.add(photo)
    db_session.commit()
    state = service._source_state_for(db_session, user_id=user.id, provider='manual_upload', source_key='lease')
    first = service.enqueue_reconciliation_job(db_session, user_id=user.id, source_state=state, photo_id=photo.id, event_kind='provider_new')
    second = service.enqueue_reconciliation_job(db_session, user_id=user.id, source_state=state, photo_id=photo.id, event_kind='provider_new')
    db_session.commit()

    assert first.id == second.id
    first.status = 'running'
    first.lease_owner = 'dead-worker'
    first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(first)
    db_session.commit()
    claimed = service._claim_reconciliation_job(db_session, user_id=user.id, worker_id='recovery-worker')

    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.lease_owner == 'recovery-worker'
    assert claimed.status == 'running'


def test_integrity_scan_persists_source_state_and_audit_event(db_session, monkeypatch):
    user = _create_user(db_session, email='integrity@example.com')
    service = IntakeSlateService()
    service.save_settings(db=db_session, user=user, payload={'enabled': True, 'album_url': 'https://photos.app.goo.gl/integrity'})
    monkeypatch.setattr(service, 'monitor_google_album', lambda _db, *, user: {'imported': 0, 'reconciliation_processed': 0})
    monkeypatch.setattr(service, 'sync_google_album_truth', lambda _db, *, user: {'album_visible_count': 0, 'stale_found': 0})

    result = service.run_integrity_scan(db_session, user=user)

    assert result['monitor']['imported'] == 0
    assert db_session.query(IntakeReconciliationJob).filter_by(user_id=user.id).count() == 0
    events = db_session.query(IntakeReconciliationEvent).filter_by(user_id=user.id, event_type='integrity_scan').all()
    assert len(events) == 1


def test_discovery_persists_entire_enumeration_before_chunked_processing(db_session, monkeypatch):
    """A chunk size bounds work, never visibility of an enumerated album."""
    user = _create_user(db_session, email='discovery-chunks@example.com')
    service = IntakeSlateService()
    service.save_settings(db=db_session, user=user, payload={
        'enabled': True,
        'album_url': 'https://photos.app.goo.gl/discovery-chunks',
        'max_new_photos_per_run': 60,
        'auto_draft_listing': False,
    })
    image_path = _make_image_file('discovery-chunk-image', 'orange')
    entries = [
        {
            'url': f'photo-{index}',
            'source_photo_id': f'discovery-{index:04d}',
            'suggested_basename': f'discovery-{index:04d}',
            'original_filename': f'{index:04d}.jpg',
            'source_order': index,
            'captured_at': (datetime.now(UTC) - timedelta(hours=1) + timedelta(seconds=index)).isoformat(),
        }
        for index in range(125)
    ]
    monkeypatch.setattr(service.google_photos, 'enumerate_photo_entries', lambda _url: GooglePhotoEnumeration(entries, enumeration_complete=True, provider_item_count=125))
    monkeypatch.setattr(service.storage, 'save_from_url', lambda *_args, **_kwargs: image_path)
    monkeypatch.setattr(service, 'classify_photo_for_intake', lambda _path: {'is_qr_candidate': False, 'is_probable_slate': False})
    monkeypatch.setattr('app.workers.tasks.drain_intake_provider_media_task.apply_async', lambda *args, **kwargs: None)

    first = service.monitor_google_album(db_session, user=user)
    assert db_session.query(IntakeProviderMedia).filter_by(user_id=user.id).count() == 125
    assert first['processing_backlog_count'] == 65
    assert first['source_caught_up'] is False

    second = service.monitor_google_album(db_session, user=user)
    third = service.monitor_google_album(db_session, user=user)

    assert second['processing_backlog_count'] == 5
    assert third['processing_backlog_count'] == 0
    assert db_session.query(IntakeProviderMedia).filter_by(user_id=user.id, processing_status='processed').count() == 125
    state = db_session.query(IntakeSourceState).filter_by(user_id=user.id).one()
    assert state.source_caught_up is True


def test_source_poll_lease_recovers_after_expiration(db_session):
    user = _create_user(db_session, email='source-poll-lease@example.com')
    service = IntakeSlateService()
    state = service._source_state_for(db_session, user_id=user.id, provider='google_photos', source_key='lease-source')
    assert service._claim_source_poll_lease(db_session, source_state=state, owner='worker-a') is True
    assert service._claim_source_poll_lease(db_session, source_state=state, owner='worker-b') is False
    state.poll_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(state)
    db_session.commit()
    assert service._claim_source_poll_lease(db_session, source_state=state, owner='worker-b') is True


def test_reconciliation_keeps_trigger_photo_when_it_is_not_last_interval_row(db_session):
    user = _create_user(db_session, email='trigger-photo@example.com')
    service = IntakeSlateService()
    slate_a, qr_a, _ = service.create_slate(db_session, user=user, payload={'session_id': 'TRIGGER', 'title': 'A'})
    slate_b, qr_b, _ = service.create_slate(db_session, user=user, payload={'session_id': 'TRIGGER', 'title': 'B'})
    captured = datetime.now(UTC) - timedelta(hours=2)
    trigger = IntakePhoto(user_id=user.id, source_provider='test', source_photo_id='trigger-middle', local_path=_make_image_file('trigger-middle'), captured_at=captured + timedelta(seconds=2), imported_at=datetime.now(UTC), metadata_json={}, is_public_listing_candidate=True)
    rows = [
        IntakePhoto(user_id=user.id, source_provider='test', source_photo_id='trigger-slate-a', local_path=_make_image_file('trigger-slate-a'), captured_at=captured, imported_at=captured, metadata_json={'qr_payload': qr_a}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
        IntakePhoto(user_id=user.id, source_provider='test', source_photo_id='trigger-photo-1', local_path=_make_image_file('trigger-photo-1'), captured_at=captured + timedelta(seconds=1), imported_at=captured, metadata_json={}, is_public_listing_candidate=True),
        trigger,
        IntakePhoto(user_id=user.id, source_provider='test', source_photo_id='trigger-photo-3', local_path=_make_image_file('trigger-photo-3'), captured_at=captured + timedelta(seconds=3), imported_at=captured, metadata_json={}, is_public_listing_candidate=True),
        IntakePhoto(user_id=user.id, source_provider='test', source_photo_id='trigger-slate-b', local_path=_make_image_file('trigger-slate-b'), captured_at=captured + timedelta(seconds=4), imported_at=captured, metadata_json={'qr_payload': qr_b}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False),
    ]
    db_session.add_all(rows)
    db_session.commit()
    result = service.reconcile_timeline(db_session, user_id=user.id, photo_id=trigger.id)
    event = db_session.get(IntakeReconciliationEvent, result['event_id'])
    notification = db_session.query(IntakeNotification).filter_by(user_id=user.id, notification_type='late_photo_added').one()

    assert event.source_media_id == trigger.id
    assert event.details_json['late_arrival'] is True
    assert slate_a.item_id in result['result']['affected_item_ids']
    assert slate_b.item_id in result['result']['affected_item_ids']
    assert notification.canonical_item_id == db_session.query(CanonicalItem).filter_by(user_id=user.id, item_id=slate_a.item_id).one().id


def test_external_listing_reconciliation_preserves_snapshot_and_creates_review(db_session):
    user = _create_user(db_session, email='published-safety@example.com')
    service = IntakeSlateService()
    slate, qr_payload, _ = service.create_slate(db_session, user=user, payload={'session_id': 'LIVE', 'title': 'Published item'})
    slate_photo = IntakePhoto(user_id=user.id, source_provider='test', source_photo_id='live-slate', local_path=_make_image_file('live-slate'), metadata_json={'qr_payload': qr_payload}, is_slate=True, is_internal_only=True, is_public_listing_candidate=False)
    product = IntakePhoto(user_id=user.id, source_provider='test', source_photo_id='live-product', local_path=_make_image_file('live-product', 'red'), metadata_json={}, is_public_listing_candidate=True)
    db_session.add_all([slate_photo, product])
    db_session.commit()
    service.rebuild_batches_for_user(db_session, user_id=user.id)
    batch = db_session.query(IntakePhotoBatch).filter_by(user_id=user.id, item_id=slate.item_id).one()
    listing = Listing(user_id=user.id, title='Locked external title', description='Do not change', listing_price=99.0, quantity=2, image_urls=['/old.jpg'], listing_images=[{'storage_path': '/old.jpg'}], ebay_listing_id='123456', status=ListingStatus.posted)
    db_session.add(listing)
    db_session.flush()
    batch.draft_listing_id = listing.id
    slate.listing_id = listing.id
    db_session.add_all([batch, slate])
    db_session.commit()

    service.refresh_drafts_for_reconciled_items(db_session, user_id=user.id, item_ids=[slate.item_id])
    db_session.refresh(listing)
    event = db_session.query(IntakeReconciliationEvent).filter_by(user_id=user.id, event_type='external_listing_update_required').one()

    assert listing.title == 'Locked external title'
    assert listing.description == 'Do not change'
    assert listing.listing_price == 99.0
    assert listing.quantity == 2
    assert listing.image_urls == ['/old.jpg']
    assert event.details_json['before']['image_urls'] == ['/old.jpg']
    assert event.details_json['proposed']['requires_explicit_marketplace_update'] is True
