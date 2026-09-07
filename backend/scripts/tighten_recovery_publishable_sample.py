"""Tighten the titles for a bounded publishable recovery sample."""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.models import Listing


TITLE_UPDATES = {
    17: "Car Tail Light Assembly Red and White with Connector Mounting Points",
    18: "Super Strong Blue Magnetic Putty Toy",
    19: "Hess Toy Fire Truck 1993 White and Red with Ladder",
    20: "Brightroom Adjustable Drawer Divider for Organizing Drawers",
    22: "Brown Leather Handbag with Orange Accents and Zipper",
    24: "Decorative Ceramic Tea Set with Floral Design and Gold Trim",
    52: "AStyle Gray T-Shirt Size L Made in Mexico",
}


def main() -> None:
    with SessionLocal() as db:
        listings = db.execute(select(Listing).where(Listing.id.in_(TITLE_UPDATES))).scalars().all()
        updated = []
        for listing in listings:
            new_title = TITLE_UPDATES.get(listing.id)
            if not new_title or listing.title == new_title:
                continue
            listing.title = new_title
            updated.append(listing.id)
        db.commit()
        print({"updated_count": len(updated), "updated_ids": updated})


if __name__ == "__main__":
    main()
