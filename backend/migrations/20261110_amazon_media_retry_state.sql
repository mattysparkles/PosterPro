-- Track bounded Amazon product-media acquisition retries. Existing cache rows
-- start with zero recorded post-deployment attempts and remain readable by the
-- current application. No media or Vine listing records are rewritten.
ALTER TABLE product_media_cache
  ADD COLUMN IF NOT EXISTS fetch_attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE product_media_cache
  ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP NULL;
