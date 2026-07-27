from datetime import date, datetime

from pydantic import BaseModel, Field, HttpUrl


class GooglePhotosImportRequest(BaseModel):
    user_id: int = Field(default=1)
    album_url: HttpUrl


class GooglePhotosWatchRequest(BaseModel):
    album_url: HttpUrl
    enabled: bool = True
    auto_enrich: bool = True


class IntakeSettingsRequest(BaseModel):
    provider: str = "google_photos"
    album_url: HttpUrl | None = None
    folder_id: str | None = None
    enabled: bool = True
    auto_draft_listing: bool = True
    require_manual_review_before_publish: bool = True
    default_item_prefix: str = "SP"
    default_box_prefix: str = "BX"
    default_location: str | None = None
    default_session_naming_pattern: str = "{date}-{location}"
    auto_increment_item_id: bool = True
    auto_increment_box_id: bool = True
    keep_same_box_mode: bool = False
    exclude_head_slate_from_public_listing_photos: bool = True
    internal_box_photos_default: bool = True
    image_seo_filename_pattern: str = "{item_id}_{seo_title}_{photo_number}"
    poll_interval_seconds: int = 300
    marketplace_defaults: dict | None = None


class IntakeSessionCreateRequest(BaseModel):
    session_id: str | None = None
    name: str | None = None
    source_album_id: str | None = None
    source_folder_id: str | None = None
    default_location: str | None = None
    item_prefix: str | None = None
    box_prefix: str | None = None
    status: str = "active"


class IntakeSlateCreateRequest(BaseModel):
    session_id: str | None = None
    item_id: str | None = None
    item_prefix: str | None = None
    box_id: str | None = None
    box_prefix: str | None = None
    location: str | None = None
    title: str | None = None
    brand: str | None = None
    model: str | None = None
    condition: str | None = None
    notes: str | None = None
    flaws: str | None = None
    weight: str | None = None
    length: str | None = None
    width: str | None = None
    height: str | None = None
    packed: bool = False
    boundary_position: str | None = "start"
    internal_notes: str | None = None
    mark_packed: bool = False
    increment_box: bool = False
    same_box: bool = False


class IntakeSlateUpdateRequest(BaseModel):
    item_id: str | None = None
    box_id: str | None = None
    location: str | None = None
    title: str | None = None
    brand: str | None = None
    model: str | None = None
    condition: str | None = None
    notes: str | None = None
    flaws: str | None = None
    weight: str | None = None
    length: str | None = None
    width: str | None = None
    height: str | None = None
    packed: bool | None = None
    boundary_position: str | None = None
    internal_notes: str | None = None
    status: str | None = None


class IntakePhotoCorrectionRequest(BaseModel):
    item_id: str | None = None
    batch_id: int | None = None
    is_slate: bool | None = None
    is_public_listing_candidate: bool | None = None
    is_internal_only: bool | None = None
    image_type: str | None = None


class IntakeBatchDraftRequest(BaseModel):
    force_regenerate: bool = False


class IntakeUnassignedAssignmentRequest(BaseModel):
    item_id: str
    photo_ids: list[int] = Field(default_factory=list)
    mark_ready_for_draft: bool = True


class IntakeBoundarySelection(BaseModel):
    photo_id: int
    item_id: str


class IntakeBoundaryApplyRequest(BaseModel):
    boundaries: list[IntakeBoundarySelection] = Field(default_factory=list)
    mark_ready_for_draft: bool = True


class IntakeBatchMergeRequest(BaseModel):
    source_batch_ids: list[int] = Field(default_factory=list)
    target_item_id: str | None = None


class IntakeBatchSplitRequest(BaseModel):
    photo_ids: list[int] = Field(default_factory=list)
    new_item_id: str | None = None
    new_box_id: str | None = None
    location: str | None = None


class IntakeTimelineReconcileRequest(BaseModel):
    photo_id: int | None = None
    full_integrity_scan: bool = False


class IntakeSlateRecoveryRunRequest(BaseModel):
    photo_ids: list[int] | None = None
    limit: int | None = Field(default=None, ge=1, le=10000)
    pipeline_version: str = Field(default="deterministic_slate_recovery_v1", min_length=1, max_length=64)


class IntakeFactUpdateRequest(BaseModel):
    value: object | None = None
    lock: bool = True


class IntakeSessionResponse(BaseModel):
    id: int
    user_id: int
    session_id: str
    name: str | None = None
    source_album_id: str | None = None
    source_folder_id: str | None = None
    default_location: str | None = None
    item_prefix: str | None = None
    box_prefix: str | None = None
    status: str
    metadata_json: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class IntakeSlateResponse(BaseModel):
    id: int
    user_id: int
    intake_session_id: int | None = None
    session_id: str | None = None
    item_id: str
    box_id: str | None = None
    location: str | None = None
    title: str | None = None
    brand: str | None = None
    model: str | None = None
    condition: str | None = None
    notes: str | None = None
    flaws: str | None = None
    weight: str | None = None
    length: str | None = None
    width: str | None = None
    height: str | None = None
    packed: bool = False
    internal_notes: str | None = None
    qr_payload_json: dict | None = None
    slate_image_id: int | None = None
    listing_id: int | None = None
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class IntakePhotoResponse(BaseModel):
    id: int
    user_id: int
    source_provider: str
    source_photo_id: str
    source_album_id: str | None = None
    source_folder_id: str | None = None
    original_filename: str | None = None
    local_path: str
    downloaded_url: str | None = None
    content_hash: str | None = None
    captured_at: datetime | None = None
    uploaded_at: datetime | None = None
    imported_at: datetime | None = None
    image_type: str | None = None
    is_slate: bool = False
    is_public_listing_candidate: bool = True
    is_internal_only: bool = False
    item_id: str | None = None
    batch_id: int | None = None
    metadata_json: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class IntakePhotoBatchResponse(BaseModel):
    id: int
    user_id: int
    intake_session_id: int | None = None
    session_id: str | None = None
    item_id: str
    slate_id: int | None = None
    first_photo_id: int | None = None
    last_photo_id: int | None = None
    photo_count: int = 0
    public_photo_count: int = 0
    internal_photo_count: int = 0
    draft_listing_id: int | None = None
    status: str
    metadata_json: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class ListingGenerateRequest(BaseModel):
    barcode: str | None = None


class ListingRevisionRequest(BaseModel):
    fields: list[str] = []
    note: str | None = None


class ListingApproveQueueRequest(BaseModel):
    listing_ids: list[int]
    marketplaces: list[str] = ["ebay"]
    confirm_live_publish: bool = False
    confirmation_phrase: str | None = None


class ListingCreateRequest(BaseModel):
    status: str | None = None
    image_urls: list[str] | None = None
    listing_images: list[dict] | None = None
    raw_photo_path: str | None = None
    storage_unit_name: str | None = None
    title: str | None = None
    description: str | None = None
    category_id: str | None = None
    category_suggestion: str | None = None
    item_specifics: dict | None = None
    tags: list[str] | None = None
    estimated_value: float | None = None
    start_price: float | None = None
    buy_it_now_price: float | None = None
    min_acceptable_offer: float | None = None
    suggested_price: float | None = None
    listing_price: float | None = None
    purchase_cost: float | None = None
    fees_estimated: float | None = None
    fees_actual: float | None = None
    shipping_cost: float | None = None
    sale_price: float | None = None
    condition: str | None = None
    condition_data: dict | None = None
    photo_quality_score: float | None = None
    quantity: int | None = None
    platform_quantities: dict | None = None
    custom_labels: list[str] | None = None
    last_refreshed: datetime | None = None
    source_type: str | None = None
    source_metadata: dict | None = None
    shipping_profile: dict | None = None
    marketplace_data: dict | None = None
    needs_review: bool | None = None
    restricted_review_required: bool | None = None
    restricted_reasons: list[str] | None = None
    detected_category_guess: str | None = None
    marketplace_allowed_status: str | None = None


class ListingUpdateRequest(ListingCreateRequest):
    pass


class MarketplaceStatusEntry(BaseModel):
    marketplace: str
    status: str
    marketplace_listing_id: str | None = None
    raw_response: dict | None = None


class ListingResponse(BaseModel):
    id: int
    user_id: int
    cluster_id: int | None
    status: str
    image_urls: list[str] | None = None
    listing_images: list[dict] | None = None
    raw_photo_path: str | None = None
    storage_unit_name: str | None = None
    title: str | None
    description: str | None
    category_id: str | None = None
    category_suggestion: str | None = None
    item_specifics: dict | None = None
    tags: list[str] | None = None
    estimated_value: float | None = None
    start_price: float | None = None
    buy_it_now_price: float | None = None
    min_acceptable_offer: float | None = None
    suggested_price: float | None
    listing_price: float | None = None
    purchase_cost: float | None = None
    fees_estimated: float | None = None
    fees_actual: float | None = None
    shipping_cost: float | None = None
    sale_price: float | None = None
    profit: float | None = None
    roi_percentage: float | None = None
    condition_data: dict | None = None
    ebay_listing_id: str | None = None
    ebay_publish_status: str | None = None
    marketplace_data: dict | None = None
    marketplace_preflight_summary: dict | None = None
    quantity: int = 1
    platform_quantities: dict | None = None
    custom_labels: list[str] | None = None
    last_refreshed: datetime | None = None
    source_type: str | None = None
    source_metadata: dict | None = None
    shipping_profile: dict | None = None
    needs_review: bool = False
    restricted_review_required: bool = False
    restricted_reasons: list[str] | None = None
    detected_category_guess: str | None = None
    marketplace_allowed_status: str | None = None
    marketplace_statuses: list[MarketplaceStatusEntry] = Field(default_factory=list)
    readiness_summary: dict = Field(default_factory=dict)
    quality_summary: dict = Field(default_factory=dict)
    latest_publish_attempt: dict | None = None

    class Config:
        from_attributes = True


class MarketplacePublishRequest(BaseModel):
    marketplaces: list[str] | None = None
    confirm_live_publish: bool = False
    confirmation_phrase: str | None = None


class EbayPublishConfirmationRequest(BaseModel):
    confirm_live_publish: bool = False
    confirmation_phrase: str | None = None


class BulkMarketplacePublishRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)
    marketplaces: list[str] | None = None
    confirm_live_publish: bool = False
    confirmation_phrase: str | None = None
    confirm_live_publish: bool = False
    confirmation_phrase: str | None = None


class MarketplacePublishResult(BaseModel):
    marketplace: str
    task_id: str | None = None
    status: str


class MarketplacePreflightIssue(BaseModel):
    code: str
    field: str | None = None
    message: str
    fix_hint: str | None = None
    severity: str = "blocker"
    retryable: bool = False


class MarketplacePayloadPreview(BaseModel):
    payload: dict = Field(default_factory=dict)
    sections: list[str] = Field(default_factory=list)


class MarketplacePreflightResponse(BaseModel):
    listing_id: int
    marketplace: str
    status: str
    blockers: list[MarketplacePreflightIssue] = Field(default_factory=list)
    warnings: list[MarketplacePreflightIssue] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    suggested_fixes: list[str] = Field(default_factory=list)
    required_operator_actions: list[str] = Field(default_factory=list)
    payload_preview: MarketplacePayloadPreview = Field(default_factory=MarketplacePayloadPreview)
    policy_summary: dict = Field(default_factory=dict)
    category_summary: dict = Field(default_factory=dict)
    shipping_summary: dict = Field(default_factory=dict)
    image_summary: dict = Field(default_factory=dict)
    pricing_summary: dict = Field(default_factory=dict)
    condition_summary: dict = Field(default_factory=dict)
    quality_summary: dict = Field(default_factory=dict)
    readiness_summary: dict = Field(default_factory=dict)
    last_checked_at: datetime | None = None
    source_version: str = "v1"


class PublishAttemptResponse(BaseModel):
    id: int
    listing_id: int
    marketplace: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    dry_run: bool = True
    preflight_status: str | None = None
    payload_snapshot: dict | None = None
    payload_hash: str | None = None
    inventory_item_sku: str | None = None
    offer_id: str | None = None
    marketplace_listing_id: str | None = None
    marketplace_status: str | None = None
    translated_error: dict | None = None
    raw_error: str | None = None
    retryable: bool = False
    retry_count: int = 0
    previous_attempt_id: int | None = None
    job_id: int | None = None
    task_id: str | None = None


class EbayAccountReadinessResponse(BaseModel):
    connected: bool
    has_refresh_token: bool
    token_status: str
    import_ready: bool
    reconnect_required: bool
    status_note: str
    payment_policy_name: str | None = None
    payment_policy_id: str | None = None
    fulfillment_policy_name: str | None = None
    fulfillment_policy_id: str | None = None
    return_policy_name: str | None = None
    return_policy_id: str | None = None
    merchant_location_key: str | None = None
    merchant_location_verified: bool = False
    merchant_location_status: str | None = None
    merchant_location_last_checked_at: datetime | None = None
    policy_sync_status: str | None = None
    policy_sync_error: str | None = None
    shipping_service_code: str | None = None
    handling_time_days: int | None = None
    local_pickup_allowed: bool = False
    calculated_shipping: bool = False
    package_weight_required: bool = True
    package_dimensions_required: bool = True
    policies_present: bool = False
    location_present: bool = False
    publish_ready: bool = False
    mode: str | None = None


class EbayPolicyCandidate(BaseModel):
    id: str
    name: str | None = None
    marketplace_id: str | None = None
    is_default: bool = False
    category_types: list[str] = Field(default_factory=list)
    raw_category_types: list[dict] = Field(default_factory=list)


class EbayPolicyCatalogResponse(BaseModel):
    marketplace_id: str = "EBAY_US"
    status: str = "ready"
    payment_policies: list[EbayPolicyCandidate] = Field(default_factory=list)
    fulfillment_policies: list[EbayPolicyCandidate] = Field(default_factory=list)
    return_policies: list[EbayPolicyCandidate] = Field(default_factory=list)
    selected: dict = Field(default_factory=dict)
    policy_settings: dict = Field(default_factory=dict)
    missing_policy_types: list[str] = Field(default_factory=list)
    sync_error: str | None = None
    last_synced_at: datetime | None = None


class EbayPolicySyncRequest(BaseModel):
    marketplace_id: str = "EBAY_US"
    create_missing_defaults: bool = False


class EbayPolicySelectRequest(BaseModel):
    marketplace_id: str = "EBAY_US"
    payment_policy_id: str | None = None
    payment_policy_name: str | None = None
    fulfillment_policy_id: str | None = None
    fulfillment_policy_name: str | None = None
    return_policy_id: str | None = None
    return_policy_name: str | None = None


class EbayMerchantLocationRequest(BaseModel):
    merchant_location_key: str | None = None
    merchant_location_location_name: str | None = None
    merchant_location_postal_code: str | None = None
    merchant_location_country: str | None = None
    merchant_location_city: str | None = None
    merchant_location_state_or_province: str | None = None
    merchant_location_phone: str | None = None
    create_if_missing: bool = False


class LaunchCandidateRequest(BaseModel):
    marketplace: str = "ebay"
    max_items: int = 10
    max_price: float = 50
    include_warning_only: bool = False
    include_local_pickup: bool = False
    include_risky_shipping: bool = False


class LaunchCandidateEntry(BaseModel):
    listing_id: int
    title: str | None = None
    price: float | None = None
    quality_score: float | None = None
    preflight_status: str | None = None
    top_warnings: list[str] = Field(default_factory=list)
    payload_preview_available: bool = False
    reason_selected: str | None = None
    reason_excluded: str | None = None
    marketplace_summary: dict = Field(default_factory=dict)


class LaunchCandidateResponse(BaseModel):
    marketplace: str
    max_items: int
    max_price: float
    include_warning_only: bool = False
    include_local_pickup: bool = False
    include_risky_shipping: bool = False
    generated_at: datetime | None = None
    candidates: list[LaunchCandidateEntry] = Field(default_factory=list)
    excluded: list[dict] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


class LaunchDrillRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)
    marketplace: str = "ebay"
    max_items: int = 10
    require_ready: bool = True
    include_payload_preview: bool = True


class LaunchDrillItemResponse(BaseModel):
    listing_id: int
    title: str | None = None
    status: str
    preflight: dict | None = None
    payload_preview: dict | None = None
    blockers: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    launch_checklist: list[dict] = Field(default_factory=list)
    reason: str | None = None


class LaunchDrillResponse(BaseModel):
    marketplace: str
    generated_at: datetime | None = None
    summary: dict = Field(default_factory=dict)
    items: list[LaunchDrillItemResponse] = Field(default_factory=list)


class EbayLaunchRepairQueueEntry(BaseModel):
    listing_id: int
    title: str | None = None
    price: float | None = None
    status: str | None = None
    current_preflight_status: str | None = None
    blocker_codes: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    photo_counts: dict = Field(default_factory=dict)
    image_status: str | None = None
    ready_for_image_preflight: bool = False
    category_status: dict = Field(default_factory=dict)
    suggested_category: dict | None = None
    required_aspects_status: dict = Field(default_factory=dict)
    shipping_status: dict = Field(default_factory=dict)
    condition_status: dict = Field(default_factory=dict)
    recommended_next_repair_action: str | None = None
    estimated_repair_difficulty: str | None = None
    quality_score: float | None = None


class EbayLaunchRepairQueueResponse(BaseModel):
    marketplace: str = "ebay"
    generated_at: datetime | None = None
    summary: dict = Field(default_factory=dict)
    items: list[EbayLaunchRepairQueueEntry] = Field(default_factory=list)


class EbayLaunchRepairActionRequest(BaseModel):
    apply_category_suggestion: bool = False
    validate_images: bool = False


class ListingPhotoActionRequest(BaseModel):
    storage_paths: list[str] = Field(default_factory=list)


class ListingPhotoSetPrimaryRequest(BaseModel):
    storage_path: str


class MarketplaceBulkPreflightMarketResult(BaseModel):
    marketplace: str
    status: str
    blocker_count: int = 0
    warning_count: int = 0
    blocker_codes: list[str] = Field(default_factory=list)
    warning_codes: list[str] = Field(default_factory=list)
    blocker_messages: list[str] = Field(default_factory=list)
    warning_messages: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    ready: bool = False
    payload_preview_available: bool = False
    cached: bool = False
    stale: bool = False
    last_checked_at: datetime | None = None
    source_version: str | None = None
    top_blocker_code: str | None = None
    top_warning_code: str | None = None
    top_blocker_message: str | None = None
    top_warning_message: str | None = None
    blockers: list[MarketplacePreflightIssue] = Field(default_factory=list)
    warnings: list[MarketplacePreflightIssue] = Field(default_factory=list)


class MarketplaceBulkPreflightListingResult(BaseModel):
    listing_id: int
    title: str | None = None
    marketplaces: dict[str, MarketplaceBulkPreflightMarketResult] = Field(default_factory=dict)
    ready_marketplaces: list[str] = Field(default_factory=list)
    blocked_marketplaces: list[str] = Field(default_factory=list)
    warning_marketplaces: list[str] = Field(default_factory=list)
    top_blocker_code: str | None = None
    top_blocker_message: str | None = None
    top_warning_code: str | None = None
    top_warning_message: str | None = None
    price: float | None = None
    category: str | None = None
    condition: str | None = None
    image_count: int | None = None
    actual_image_count: int | None = None
    package_weight: float | None = None
    package_dimensions: dict | None = None
    last_preflight_at: datetime | None = None


class MarketplaceBulkPreflightSummary(BaseModel):
    total_listings_checked: int = 0
    total_marketplaces_checked: int = 0
    ready_for_ebay: int = 0
    ready_for_facebook: int = 0
    ready_with_warnings: int = 0
    blocked: int = 0
    blocked_listings: int = 0
    warning_only_listings: int = 0
    ready_listings: int = 0
    missing_photos: int = 0
    missing_price: int = 0
    missing_shipping: int = 0
    missing_policies: int = 0
    missing_ebay_aspects: int = 0
    weak_pricing: int = 0
    reference_images_only: int = 0
    preflight_failed: int = 0
    blocker_codes: dict[str, int] = Field(default_factory=dict)
    warning_codes: dict[str, int] = Field(default_factory=dict)
    most_common_blocker: str | None = None
    most_common_warning: str | None = None
    timestamp: datetime | None = None


class BulkMarketplacePreflightRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)
    marketplaces: list[str] = Field(default_factory=lambda: ["ebay", "facebook"])
    force_refresh: bool = False
    only_drafts: bool = False
    selected_statuses: list[str] | None = None
    only_missing_preflight: bool = False
    only_stale_preflight: bool = False
    only_ready_candidates: bool = False
    only_blocked_candidates: bool = False


class BulkMarketplacePreflightResponse(BaseModel):
    items: list[MarketplaceBulkPreflightListingResult] = Field(default_factory=list)
    summary: MarketplaceBulkPreflightSummary = Field(default_factory=MarketplaceBulkPreflightSummary)
    marketplaces: list[str] = Field(default_factory=list)
    generated_at: datetime | None = None


class BulkMarketplacePublishReadyRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)
    marketplaces: list[str] = Field(default_factory=lambda: ["ebay", "facebook"])
    allow_warnings: bool = False
    dry_run: bool = True
    force_preflight_refresh: bool = False
    skip_already_queued: bool = True
    confirm_live_publish: bool = False
    confirmation_phrase: str | None = None


class BulkMarketplacePublishReadyMarketResult(BaseModel):
    marketplace: str
    status: str
    task_id: str | None = None
    error: str | None = None
    error_details: list[dict] | dict | None = None
    warnings: list[dict] = Field(default_factory=list)
    preflight: dict | None = None


class BulkMarketplacePublishReadyListingResult(BaseModel):
    listing_id: int
    title: str | None = None
    marketplaces: dict[str, BulkMarketplacePublishReadyMarketResult] = Field(default_factory=dict)
    ready_marketplaces: list[str] = Field(default_factory=list)
    blocked_marketplaces: list[str] = Field(default_factory=list)
    warning_marketplaces: list[str] = Field(default_factory=list)


class BulkMarketplacePublishReadySummary(BaseModel):
    queued: int = 0
    skipped_blocked: int = 0
    skipped_warning_requires_confirmation: int = 0
    skipped_unsupported_marketplace: int = 0
    skipped_already_queued: int = 0
    failed: int = 0
    dry_run_ready: int = 0
    dry_run_blocked: int = 0


class BulkMarketplacePublishReadyResponse(BaseModel):
    items: list[BulkMarketplacePublishReadyListingResult] = Field(default_factory=list)
    summary: BulkMarketplacePublishReadySummary = Field(default_factory=BulkMarketplacePublishReadySummary)
    markets: list[str] = Field(default_factory=list)
    dry_run: bool = True
    allow_warnings: bool = False
    skip_already_queued: bool = True
    generated_at: datetime | None = None


class ListingApprovalResponse(BaseModel):
    listing: ListingResponse
    auto_publish_after_approval: bool = False
    approval_publishable: bool = False
    approval_blockers: dict[str, list[dict]] = Field(default_factory=dict)
    results: list[MarketplacePublishResult] = Field(default_factory=list)


class BulkListingApproveRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)


class BulkListingApproveResponse(BaseModel):
    approvals: list[ListingApprovalResponse] = Field(default_factory=list)


class BulkMarketplacePublishResponse(BaseModel):
    results: list[dict] = Field(default_factory=list)


class PricingBulkRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)
    action: str = "refresh"
    manual_comp: dict | None = None


class PricingApplyRequest(BaseModel):
    strategy: str = "recommended"
    override_reason: str | None = None


class ConnectMarketplaceResponse(BaseModel):
    marketplace: str
    auth: dict


class SoldSyncRequest(BaseModel):
    listing_ids: list[int] | None = None


class BatchStorageUnitUrlRequest(BaseModel):
    image_urls: list[HttpUrl] = Field(default_factory=list)
    user_id: int = 1
    storage_unit_name: str | None = None
    overnight_mode: bool = False


class StorageUnitBatchResponse(BaseModel):
    id: int
    user_id: int
    storage_unit_name: str | None = None
    status: str
    overnight_mode: bool
    total_items: int
    processed_items: int
    error_message: str | None = None
    pipeline_task_id: str | None = None

    class Config:
        from_attributes = True


class InventoryBulkEditRequest(BaseModel):
    listing_ids: list[int] = Field(default_factory=list)
    quantity: int | None = None
    platform_quantities: dict | None = None
    add_labels: list[str] | None = None
    remove_labels: list[str] | None = None
    delist: bool = False
    relist: bool = False


class InventoryFilterRequest(BaseModel):
    label: str | None = None
    stale: bool = False
    quantity_gt_one: bool = False
    search: str | None = None


class InventoryBulkRequest(BaseModel):
    action: str = Field(description="edit|delist|relist|label|mark_sold|refresh|autobump")
    listing_ids: list[int] = Field(default_factory=list)
    filters: InventoryFilterRequest | None = None
    payload: dict | None = None
    user_id: int = 1


class BulkJobResponse(BaseModel):
    job_id: str
    action: str
    status: str
    total_items: int
    processed_items: int
    error_count: int = 0
    errors: list[dict] = Field(default_factory=list)


class SaleDetectionConfigRequest(BaseModel):
    marketplaces: list[str] = Field(default_factory=list)


class SaleDetailsUpdateRequest(BaseModel):
    fees_actual: float | None = None
    shipping_cost: float | None = None
    promotional_fees: float | None = None
    marketplace_fees: float | None = None
    notes: str | None = None


class SaleReconcileRequest(BaseModel):
    listing_id: int | None = None
    notes: str | None = None

class PhotoEditRequest(BaseModel):
    brightness: float = 1.0
    contrast: float = 1.0
    filter_name: str = "none"
    crop_x: int | None = None
    crop_y: int | None = None
    crop_width: int | None = None
    crop_height: int | None = None


class PhotoEditResponse(BaseModel):
    image_url: str
    image_urls: list[str]


class ListingTemplateCreateRequest(BaseModel):
    user_id: int = 1
    name: str
    category_id: str | None = None
    is_category_default: bool = False
    fields: dict = Field(default_factory=dict)


class ListingTemplateApplyRequest(BaseModel):
    template_id: int


class ListingTemplateResponse(BaseModel):
    id: int
    user_id: int
    name: str
    category_id: str | None = None
    is_category_default: bool
    fields: dict = Field(default_factory=dict)

    class Config:
        from_attributes = True


class AuthRegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class AuthLoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class AuthForgotPasswordRequest(BaseModel):
    email: str


class AuthPasswordResetRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class AuthChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class AuthViewModeRequest(BaseModel):
    view_as_regular: bool = False


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str | None = None
    is_admin: bool = False
    effective_is_admin: bool = False
    view_as_regular: bool = False
    role: str = "public"
    can_access_vine_import: bool = False
    workflow_preferences: dict = Field(default_factory=dict)
    vine_enforce_six_month_lock: bool = True
    sold_sync_preferences: dict = Field(default_factory=dict)
    ebay_marketplace_policy_settings: dict = Field(default_factory=dict)

    class Config:
        from_attributes = True


class AuthSessionResponse(BaseModel):
    user: UserResponse
    is_bootstrap_admin: bool = False


class UserUpdateRequest(BaseModel):
    full_name: str | None = None
    review_before_publish: bool | None = None
    auto_publish_after_approval: bool | None = None
    bulk_approval_enabled: bool | None = None
    listing_preview_mode: str | None = None
    default_preview_marketplace: str | None = None
    vine_enforce_six_month_lock: bool | None = None
    sold_out_delist_everywhere: bool | None = None
    out_of_stock_delist_everywhere: bool | None = None
    remove_media_on_sold_out: bool | None = None
    ebay_marketplace_policy_settings: dict | None = None


class ServerSettingsUpdateRequest(BaseModel):
    app_base_url: str | None = None
    openai_api_key: str | None = None
    photoroom_api_key: str | None = None
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_runame: str | None = None
    ebay_redirect_uri: str | None = None
    storage_root: str | None = None
    environment: str | None = None
    autonomous_dry_run: bool | None = None
    autonomous_crosspost_enabled: bool | None = None
    automation_bridge_enabled: bool | None = None
    automation_bridge_url: str | None = None
    automation_bridge_timeout_seconds: int | None = None
    automation_bridge_api_key: str | None = None
    sale_detection_enabled: bool | None = None
    sale_detection_dry_run: bool | None = None
    sale_detection_poll_minutes: int | None = None
    amazon_vine_import_enabled: bool | None = None
    amazon_vine_import_premium_only: bool | None = None
    amazon_media_lookup_enabled: bool | None = None
    amazon_media_page_fallback_enabled: bool | None = None
    amazon_marketplace_region: str | None = None
    amazon_media_fetch_mode: str | None = None
    amazon_media_rate_limit_per_minute: int | None = None
    amazon_paapi_access_key: str | None = None
    amazon_paapi_secret_key: str | None = None
    amazon_paapi_partner_tag: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str | None = None
    smtp_use_tls: bool | None = None


class HostedPagesUpdateRequest(BaseModel):
    brand_name: str | None = None
    active_theme_id: str | None = None
    pages: dict | None = None
    privacy_policy_slug: str | None = None
    privacy_policy_title: str | None = None
    privacy_policy_html: str | None = None
    trust_center_slug: str | None = None
    trust_center_title: str | None = None
    trust_center_html: str | None = None
    operator_onboarding_slug: str | None = None
    operator_onboarding_title: str | None = None
    operator_onboarding_html: str | None = None
    ebay_auth_accepted_slug: str | None = None
    ebay_auth_accepted_title: str | None = None
    ebay_auth_accepted_html: str | None = None
    ebay_auth_declined_slug: str | None = None
    ebay_auth_declined_title: str | None = None
    ebay_auth_declined_html: str | None = None


class HostedPagesThemeImportRequest(BaseModel):
    theme_pack_json: str
    replace_existing: bool = False
    activate_imported: bool = True


class HostedPagesPublishRequest(BaseModel):
    page_keys: list[str] = Field(default_factory=list)


class EbayManualConnectRequest(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in_seconds: int | None = None
    external_account_id: str | None = None


class MarketplaceConnectionStatusResponse(BaseModel):
    marketplace: str
    supports_oauth: bool = False
    connection_mode: str = "server_config"
    connected: bool = False
    available: bool = False
    enabled_for_publishing: bool = False
    enabled_for_sale_detection: bool = False
    external_account_id: str | None = None
    token_expires_at: datetime | None = None
    has_refresh_token: bool = False
    token_status: str | None = None
    import_ready: bool = False
    reconnect_required: bool = False
    status_note: str | None = None
    publish_support_level: str | None = None
    publish_support_label: str | None = None
    publish_support_note: str | None = None
    import_support_level: str | None = None
    import_support_label: str | None = None
    import_support_note: str | None = None
    sales_sync_support_level: str | None = None
    sales_sync_support_label: str | None = None
    sales_sync_support_note: str | None = None
    display_name: str | None = None
    account_handle: str | None = None
    notes: str | None = None
    workflow_state: str | None = None
    import_mode: str | None = None
    publish_mode: str | None = None
    shipping_scope: str | None = None
    renewal_mode: str | None = None
    support_url: str | None = None
    bridge_account_key: str | None = None
    import_listing_limit: int | None = None
    can_publish: bool = False
    can_sync_sales: bool = False
    ui_priority: int = 99
    ui_state_tone: str = "default"
    ui_primary_action: str | None = None
    ui_secondary_actions: list[str] = Field(default_factory=list)


class MarketplaceConnectionUpdateRequest(BaseModel):
    display_name: str | None = None
    account_handle: str | None = None
    notes: str | None = None
    workflow_state: str | None = None
    import_mode: str | None = None
    publish_mode: str | None = None
    shipping_scope: str | None = None
    renewal_mode: str | None = None
    support_url: str | None = None
    bridge_account_key: str | None = None
    import_listing_limit: int | None = None


class CrosspostQueueRequest(BaseModel):
    marketplaces: list[str] = Field(default_factory=list)
    requested_mode: str | None = None


class CrosspostPreviewEntry(BaseModel):
    marketplace: str
    execution_mode: str
    payload: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class CrosspostJobResponse(BaseModel):
    id: int
    user_id: int
    listing_id: int
    source_marketplace: str | None = None
    target_marketplaces: list[str] = Field(default_factory=list)
    requested_mode: str | None = None
    status: str
    execution_plan: dict | None = None
    result_summary: dict | None = None
    task_id: str | None = None
    last_error: str | None = None
    can_retry: bool = True
    can_cancel: bool = False
    operator_note: str | None = None
    operator_action: str | None = None
    review_required_count: int = 0
    submitted_count: int = 0
    failed_target_count: int = 0
    target_outcomes: list[dict] = Field(default_factory=list)
    ui_state_tone: str = "default"
    ui_primary_action: str | None = None
    ui_secondary_actions: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class MarketplaceImportJobCreateRequest(BaseModel):
    source_marketplace: str
    source_listing_reference: str | None = None
    import_mode: str = "manual"
    payload: dict = Field(default_factory=dict)


class MarketplaceBulkImportRequest(BaseModel):
    """Compatibility payload for the former all-marketplace import control."""
    marketplaces: list[str] = Field(default_factory=list)
    max_listings: int | None = None


class MarketplaceImportJobResponse(BaseModel):
    id: int
    user_id: int
    source_marketplace: str
    source_listing_reference: str | None = None
    import_mode: str
    status: str
    payload: dict | None = None
    normalized_preview: dict | None = None
    created_listing_id: int | None = None
    task_id: str | None = None
    last_error: str | None = None
    is_stale: bool = False
    can_retry: bool = True
    can_cancel: bool = False
    operator_note: str | None = None
    operator_action: str | None = None
    review_required_count: int = 0
    review_items: list[dict] = Field(default_factory=list)
    ui_state_tone: str = "default"
    ui_primary_action: str | None = None
    ui_secondary_actions: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class BulkMarketplaceImportSkip(BaseModel):
    marketplace: str
    reason: str


class BulkMarketplaceImportRequest(BaseModel):
    marketplaces: list[str] | None = None
    max_listings: int | None = None


class BulkMarketplaceImportResponse(BaseModel):
    jobs: list[MarketplaceImportJobResponse] = Field(default_factory=list)
    skipped: list[BulkMarketplaceImportSkip] = Field(default_factory=list)


class MarketplaceJobsStatusSummary(BaseModel):
    total: int = 0
    queued: int = 0
    running: int = 0
    failed: int = 0
    completed: int = 0
    canceled: int = 0


class MarketplaceJobsOverviewResponse(BaseModel):
    import_jobs: list[MarketplaceImportJobResponse] = Field(default_factory=list)
    crosspost_jobs: list[CrosspostJobResponse] = Field(default_factory=list)
    import_summary: MarketplaceJobsStatusSummary = Field(default_factory=MarketplaceJobsStatusSummary)
    crosspost_summary: MarketplaceJobsStatusSummary = Field(default_factory=MarketplaceJobsStatusSummary)


class AutomationBridgeSmokeTestResponse(BaseModel):
    ok: bool
    status: str
    message: str | None = None
    bridge_url: str | None = None
    checked_url: str | None = None
    http_status: int | None = None
    response: dict | str | None = None
    errors: list[str] = Field(default_factory=list)


class BridgeMarketplaceAccountUpsertRequest(BaseModel):
    display_name: str | None = None
    login_handle: str | None = None
    credential_secret: str | None = None
    notes: str | None = None
    provider_enabled: bool = False
    browser_enabled: bool = False
    session_state: str = "draft"
    session_payload: dict = Field(default_factory=dict)
    expires_at: str | None = None


class BridgeMarketplaceAccountSessionRequest(BaseModel):
    session_state: str = "draft"
    session_payload: dict = Field(default_factory=dict)
    expires_at: str | None = None
    last_tested_at: str | None = None
    notes: str | None = None


class BridgeMarketplaceAccountConnectRequest(BaseModel):
    display_name: str | None = None
    login_handle: str | None = None
    credential_secret: str | None = None
    notes: str | None = None
    provider_enabled: bool = False
    browser_enabled: bool = True
    expires_at: str | None = None
    wait_timeout_seconds: int = 300


class BridgeDesktopAccessResponse(BaseModel):
    token: str
    websocket_path: str
    expires_at: str


class BridgeMarketplaceAccountResponse(BaseModel):
    account_id: str
    marketplace: str
    account_key: str
    display_name: str | None = None
    login_handle: str | None = None
    notes: str | None = None
    provider_enabled: bool = False
    browser_enabled: bool = False
    credential_configured: bool = False
    session_state: str = "draft"
    session_payload: dict = Field(default_factory=dict)
    expires_at: str | None = None
    last_tested_at: str | None = None
    created_at: str
    updated_at: str


class BridgeMarketplaceAccountsEnvelope(BaseModel):
    accounts: list[BridgeMarketplaceAccountResponse] = Field(default_factory=list)


class BridgeMarketplaceConnectSessionResponse(BaseModel):
    connect_session_id: str
    marketplace: str
    account_key: str
    display_name: str | None = None
    login_handle: str | None = None
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    wait_timeout_seconds: int = 300
    message: str | None = None
    error: str | None = None
    result: BridgeMarketplaceAccountResponse | None = None
    desktop_access: BridgeDesktopAccessResponse | None = None


class ActiveBridgeConnectSessionSummaryResponse(BaseModel):
    connect_session_id: str
    marketplace: str
    account_key: str
    display_name: str | None = None
    login_handle: str | None = None
    status: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    wait_timeout_seconds: int = 300
    message: str | None = None
    error: str | None = None


class ServerReadinessResponse(BaseModel):
    openai_configured: bool = False
    photoroom_configured: bool = False
    ebay_oauth_configured: bool = False
    storage_root_configured: bool = False
    session_secret_configured: bool = False
    amazon_vine_import_enabled: bool = False
    amazon_media_lookup_enabled: bool = False
    amazon_paapi_configured: bool = False


class VineImportItemResponse(BaseModel):
    id: int
    batch_id: int
    user_id: int
    order_number: str | None = None
    asin: str | None = None
    product_name: str | None = None
    order_type: str | None = None
    order_date: date | None = None
    shipped_date: date | None = None
    cancelled_date: date | None = None
    estimated_tax_value: float | None = None
    eligible_after: date | None = None
    eligibility_status: str
    raw_row_json: dict | None = None
    parse_warnings_json: list[str] | None = None
    media_status: str | None = None
    media_asset_ids_json: list[int] | None = None
    restricted_review_required: bool = False
    restricted_reasons: list[str] | None = None
    detected_category_guess: str | None = None
    marketplace_allowed_status: str | None = None
    inventory_item_id: int | None = None
    listing_id: int | None = None
    source_confidence: str = "high"
    reviewed: bool = False
    brand: str | None = None
    category: str | None = None
    source_status: str | None = None
    review_deadline: date | None = None
    item_url: str | None = None
    manual_amazon_url: str | None = None
    amazon_match_status: str | None = None
    amazon_match_confidence: str | None = None
    amazon_match_asin: str | None = None
    amazon_match_title: str | None = None
    amazon_source_page_url: str | None = None
    image_import_status: str | None = None
    image_import_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class VineImportBatchResponse(BaseModel):
    id: int
    user_id: int
    filename: str
    source_type: str
    report_year: int | None = None
    status: str
    parsed_count: int
    eligible_count: int
    locked_count: int
    cancelled_count: int
    error_count: int
    drafts_created_count: int = 0
    stats_json: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[VineImportItemResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


class VineImportActionRequest(BaseModel):
    item_ids: list[int] = Field(default_factory=list)
    new_only: bool = True
    include_locked: bool = True
    include_cancelled: bool = False
    fetch_media_first: bool = False
    require_media_for_asin: bool = False
    allow_drafts_without_media: bool = False


class VineImportItemUpdateRequest(BaseModel):
    reviewed: bool | None = None
    marketplace_allowed_status: str | None = None
    restricted_review_required: bool | None = None
    restricted_reasons: list[str] | None = None
    manual_amazon_url: str | None = None


class AccountSetupSummaryResponse(BaseModel):
    user: UserResponse
    ready_to_publish_count: int = 0
    total_listings: int = 0
    connected_marketplaces: int = 0
    has_templates: bool = False
    account_profile_complete: bool = False
    marketplace_connections: list[MarketplaceConnectionStatusResponse] = Field(default_factory=list)
    active_bridge_connect_session: ActiveBridgeConnectSessionSummaryResponse | None = None
    server_readiness: ServerReadinessResponse
