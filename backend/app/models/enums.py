import enum


class ListingStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    posted = "posted"
    rejected = "rejected"


class EbayPublishStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    POSTING = "POSTING"
    POSTED = "POSTED"
    FAILED = "FAILED"


class MarketplaceName(str, enum.Enum):
    ebay = "ebay"
    facebook = "facebook"
    mercari = "mercari"
    poshmark = "poshmark"
