import enum


class ListingStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    posted = "posted"
    rejected = "rejected"
    INGESTED = "INGESTED"
    PROCESSED = "PROCESSED"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class EbayPublishStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    POSTING = "POSTING"
    POSTED = "POSTED"
    FAILED = "FAILED"


class MarketplaceName(str, enum.Enum):
    ebay = "ebay"
    etsy = "etsy"
    facebook = "facebook"
    mercari = "mercari"
    poshmark = "poshmark"
    depop = "depop"
    whatnot = "whatnot"
    vinted = "vinted"


class MarketplaceListingStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    UPDATED = "UPDATED"
    FAILED = "FAILED"
    DELETED = "DELETED"
    PENDING = "PENDING"
