import enum


class ListingStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    posted = "posted"
    rejected = "rejected"


class MarketplaceName(str, enum.Enum):
    ebay = "ebay"
    facebook = "facebook"
    mercari = "mercari"
    poshmark = "poshmark"
