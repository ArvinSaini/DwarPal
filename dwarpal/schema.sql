-- DwarPal schema. Money is integer paise. Timestamps are unix seconds.

CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY,
  razorpay_item_id TEXT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  price_paise INTEGER NOT NULL CHECK (price_paise >= 0),
  currency TEXT NOT NULL DEFAULT 'INR',
  availability TEXT NOT NULL CHECK (availability IN ('in_stock', 'out_of_stock')),
  category TEXT,
  attributes TEXT NOT NULL DEFAULT '{}',
  tags TEXT NOT NULL DEFAULT '[]',
  recommend_when TEXT,
  url TEXT,
  image_url TEXT,
  source TEXT NOT NULL CHECK (source IN ('seed', 'razorpay')),
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS enrichments (
  id TEXT PRIMARY KEY,
  product_id TEXT NOT NULL REFERENCES products(id),
  proposal TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at INTEGER NOT NULL,
  decided_at INTEGER
);

CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  api_key_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mandates (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id),
  currency TEXT NOT NULL DEFAULT 'INR',
  per_txn_cap_paise INTEGER NOT NULL CHECK (per_txn_cap_paise >= 0),
  daily_cap_paise INTEGER NOT NULL CHECK (daily_cap_paise >= 0),
  total_cap_paise INTEGER NOT NULL CHECK (total_cap_paise >= 0),
  categories TEXT NOT NULL DEFAULT '[]',
  starts_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mandates_agent ON mandates(agent_id, status);

CREATE TABLE IF NOT EXISTS policy (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id),
  mandate_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('not_ready_for_payment', 'requires_review', 'ready_for_payment', 'payment_pending', 'completed', 'canceled')),
  line_items TEXT NOT NULL DEFAULT '[]',
  totals TEXT NOT NULL DEFAULT '{}',
  messages TEXT NOT NULL DEFAULT '[]',
  offers TEXT NOT NULL DEFAULT '[]',
  last_decision TEXT,
  idempotency_key TEXT,
  create_body_hash TEXT,
  complete_key TEXT,
  link_id TEXT,
  link_url TEXT,
  link_expire_at INTEGER,
  order_id TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sessions_idem ON sessions(agent_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS reservations (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  mandate_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
  state TEXT NOT NULL CHECK (state IN ('reserved', 'committed', 'released')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reservations_mandate ON reservations(mandate_id, state);
CREATE INDEX IF NOT EXISTS idx_reservations_session ON reservations(session_id);

CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  total_paise INTEGER NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved', 'declined')),
  note TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_session ON reviews(session_id);

CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  razorpay_payment_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('captured', 'failed')),
  amount_paise INTEGER NOT NULL,
  error_code TEXT,
  error_description TEXT,
  attempt INTEGER NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE (session_id, razorpay_payment_id)
);

CREATE TABLE IF NOT EXISTS refunds (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  mandate_id TEXT,
  razorpay_payment_id TEXT NOT NULL,
  razorpay_refund_id TEXT NOT NULL,
  amount_paise INTEGER NOT NULL CHECK (amount_paise >= 1),
  reason TEXT NOT NULL,
  reference TEXT NOT NULL,
  status TEXT NOT NULL,
  actor TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE (session_id, reference)
);
CREATE INDEX IF NOT EXISTS idx_refunds_mandate ON refunds(mandate_id);

CREATE TABLE IF NOT EXISTS ledger (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  type TEXT NOT NULL,
  actor TEXT NOT NULL,
  session_id TEXT,
  payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_session ON ledger(session_id);

CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY,
  received_at INTEGER NOT NULL
);
