CREATE TABLE IF NOT EXISTS ai_daily_budgets (
 id SERIAL PRIMARY KEY, budget_date DATE NOT NULL, pool VARCHAR(16) NOT NULL,
 safe_ceiling INTEGER NOT NULL, reserved_tokens INTEGER NOT NULL DEFAULT 0,
 actual_tokens INTEGER NOT NULL DEFAULT 0, waiting_count INTEGER NOT NULL DEFAULT 0,
 created_at TIMESTAMP NOT NULL DEFAULT NOW(), updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
 CONSTRAINT uq_ai_daily_budget_pool UNIQUE (budget_date, pool)
);
CREATE INDEX IF NOT EXISTS ix_ai_daily_budgets_date ON ai_daily_budgets(budget_date);
