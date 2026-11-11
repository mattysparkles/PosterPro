ALTER TABLE sales ADD COLUMN IF NOT EXISTS fees_actual DOUBLE PRECISION NULL;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS shipping_cost DOUBLE PRECISION NULL;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS promotional_fees DOUBLE PRECISION NULL;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS marketplace_fees DOUBLE PRECISION NULL;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS profit DOUBLE PRECISION NULL;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS roi_percentage DOUBLE PRECISION NULL;

UPDATE sales AS s
SET
    fees_actual = COALESCE(s.fees_actual, l.fees_actual),
    shipping_cost = COALESCE(s.shipping_cost, l.shipping_cost),
    profit = COALESCE(
        s.profit,
        ROUND(
            (
                COALESCE(s.amount, 0)
                - COALESCE(l.purchase_cost, 0)
                - COALESCE(s.fees_actual, l.fees_actual, 0)
                - COALESCE(s.shipping_cost, l.shipping_cost, 0)
                - COALESCE(s.promotional_fees, 0)
                - COALESCE(s.marketplace_fees, 0)
            )::numeric,
            2
        )::double precision
    ),
    roi_percentage = COALESCE(
        s.roi_percentage,
        CASE
            WHEN (
                COALESCE(l.purchase_cost, 0)
                + COALESCE(s.fees_actual, l.fees_actual, 0)
                + COALESCE(s.shipping_cost, l.shipping_cost, 0)
                + COALESCE(s.promotional_fees, 0)
                + COALESCE(s.marketplace_fees, 0)
            ) > 0
            THEN ROUND(
                (
                    (
                        COALESCE(s.amount, 0)
                        - COALESCE(l.purchase_cost, 0)
                        - COALESCE(s.fees_actual, l.fees_actual, 0)
                        - COALESCE(s.shipping_cost, l.shipping_cost, 0)
                        - COALESCE(s.promotional_fees, 0)
                        - COALESCE(s.marketplace_fees, 0)
                    )
                    /
                    (
                        COALESCE(l.purchase_cost, 0)
                        + COALESCE(s.fees_actual, l.fees_actual, 0)
                        + COALESCE(s.shipping_cost, l.shipping_cost, 0)
                        + COALESCE(s.promotional_fees, 0)
                        + COALESCE(s.marketplace_fees, 0)
                    )
                )::numeric * 100,
                2
            )::double precision
            ELSE NULL
        END
    )
FROM listings AS l
WHERE s.listing_id = l.id;
