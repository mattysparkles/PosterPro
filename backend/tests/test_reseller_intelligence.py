import os
from datetime import datetime, timedelta
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite:///./test_reseller_intelligence.db"

from app.core.database import Base, SessionLocal, engine
from app.models.models import Cluster, Listing, User
from app.services.prediction_service import PredictionService
from app.services.pricing_intelligence_service import PricingIntelligenceService
from app.services.profit_service import ProfitService

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def seed_listing(sale_price=None, category="Sneakers"):
    db = SessionLocal()
    user = User(email=f"intel-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    user_id = user.id

    cluster = Cluster(user_id=user.id, title_hint=category)
    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    listing = Listing(
        user_id=user.id,
        cluster_id=cluster.id,
        title="Nike Air Max",
        description="Great shape",
        category_suggestion=category,
        listing_price=65,
        suggested_price=62,
        purchase_cost=20,
        tags=["nike", "air", "running", "mens"],
        condition="Like New",
        photo_quality_score=0.9,
        created_at=datetime.utcnow() - timedelta(days=10),
        updated_at=datetime.utcnow(),
    )
    if sale_price is not None:
        listing.sale_price = sale_price
        listing.sold_at = datetime.utcnow()
    db.add(listing)
    db.commit()
    db.refresh(listing)
    listing_id = listing.id
    db.close()
    return listing_id, user_id


def test_profit_calculation():
    listing_id, _ = seed_listing(sale_price=80)
    db = SessionLocal()
    listing = db.get(Listing, listing_id)

    result = ProfitService().calculate_profit(listing)

    assert result["profit"] > 0
    assert result["roi_percentage"] > 0
    db.close()


def test_pricing_recommendation_accuracy_signal():
    listing_id, user_id = seed_listing(category="Jackets")
    db = SessionLocal()

    # add historical sold comps to improve confidence
    for sale in [70, 74, 72, 69, 75, 73]:
        comp = Listing(
            user_id=user_id,
            cluster_id=db.get(Listing, listing_id).cluster_id,
            category_suggestion="Jackets",
            title="Comp",
            listing_price=70,
            sale_price=sale,
            purchase_cost=25,
        )
        db.add(comp)
    db.commit()

    rec = PricingIntelligenceService().recommend_price(db, listing_id)

    assert rec["recommended_price"] > 0
    assert rec["confidence"] >= 0.8
    db.close()


def test_prediction_sanity():
    listing_id, _ = seed_listing()
    db = SessionLocal()

    pred = PredictionService().predict_sell_through(db, listing_id)

    assert 0 <= pred["probability_sale_7d"] <= 1
    assert 0 <= pred["probability_sale_30d"] <= 1
    assert pred["probability_sale_30d"] >= pred["probability_sale_7d"]
    db.close()
