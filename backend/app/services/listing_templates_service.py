from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.models import Listing, ListingTemplate


class ListingTemplateService:
    ALLOWED_FIELDS = {
        "title",
        "description",
        "category_id",
        "condition",
        "listing_price",
        "suggested_price",
        "shipping_cost",
        "quantity",
        "item_specifics",
        "custom_labels",
        "tags",
    }

    def list_templates(self, db: Session, user_id: int, category_id: str | None = None) -> list[ListingTemplate]:
        query = select(ListingTemplate).where(ListingTemplate.user_id == user_id)
        if category_id:
            query = query.where(or_(ListingTemplate.category_id == None, ListingTemplate.category_id == category_id))  # noqa: E711
        return db.execute(query.order_by(ListingTemplate.is_category_default.desc(), ListingTemplate.name.asc())).scalars().all()

    def create_template(
        self,
        db: Session,
        *,
        user_id: int,
        name: str,
        category_id: str | None,
        is_category_default: bool,
        fields: dict,
    ) -> ListingTemplate:
        clean_fields = {k: v for k, v in (fields or {}).items() if k in self.ALLOWED_FIELDS}
        template = ListingTemplate(
            user_id=user_id,
            name=name.strip() or "Template",
            category_id=category_id,
            is_category_default=is_category_default,
            fields=clean_fields,
        )
        if is_category_default and category_id:
            existing_defaults = db.execute(
                select(ListingTemplate).where(
                    and_(
                        ListingTemplate.user_id == user_id,
                        ListingTemplate.category_id == category_id,
                        ListingTemplate.is_category_default == True,  # noqa: E712
                    )
                )
            ).scalars().all()
            for row in existing_defaults:
                row.is_category_default = False
                db.add(row)

        db.add(template)
        db.commit()
        db.refresh(template)
        return template

    def apply_template(self, db: Session, listing: Listing, template: ListingTemplate) -> Listing:
        for key, value in (template.fields or {}).items():
            if key in self.ALLOWED_FIELDS:
                setattr(listing, key, value)
        if template.category_id and not listing.category_id:
            listing.category_id = template.category_id
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return listing


listing_template_service = ListingTemplateService()
