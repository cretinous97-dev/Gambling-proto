-- Naktsang Casino - PostgreSQL schema
--
-- GENERATED FILE - do not edit by hand.
--   regenerate:  make schema-sql
--   verify:      make test   (a test fails if this file is out of date)
--
-- The application creates these tables itself on first boot (`create_all`),
-- which is convenient for development and is NOT how you should manage the
-- schema of a database holding player money. On a real deployment:
--
--   1. create the database and a role that owns it
--   2. run this file once, or let Alembic manage the same shape going forward
--   3. never let the application role own the schema in production
--
-- Money columns are BIGINT minor units (cents) - never floating point. The
-- ledger is the source of truth; `balances` is a materialised cache of it, and
-- `ledger_entries` sums to zero per transaction by construction.

CREATE TYPE accountkind AS ENUM ('user_available', 'user_bonus', 'user_locked', 'house_revenue', 'bonus_pool', 'payment_clearing', 'rakeback_pool', 'chargeback_loss', 'fee_income');

CREATE TYPE depositstatus AS ENUM ('pending', 'requires_action', 'succeeded', 'failed', 'cancelled', 'chargeback');

CREATE TYPE kycstatus AS ENUM ('none', 'pending', 'verified', 'rejected');

CREATE TYPE paymentmethod AS ENUM ('card', 'bank_transfer', 'crypto_btc', 'crypto_eth', 'crypto_usdt', 'ewallet');

CREATE TYPE roundstatus AS ENUM ('betting', 'running', 'crashed', 'settled');

CREATE TYPE txstatus AS ENUM ('pending', 'posted', 'reversed');

CREATE TYPE txtype AS ENUM ('deposit', 'deposit_bonus', 'withdrawal_hold', 'withdrawal_settled', 'withdrawal_refund', 'withdrawal_fee', 'bet_stake', 'bet_payout', 'bet_refund', 'bonus_claim', 'bonus_expiry', 'rakeback', 'admin_adjustment', 'chargeback');

CREATE TYPE userrole AS ENUM ('player', 'support', 'admin');

CREATE TYPE withdrawalstatus AS ENUM ('requested', 'under_review', 'approved', 'rejected', 'paid', 'cancelled');

CREATE TABLE app_settings (
	key VARCHAR(64) NOT NULL, 
	value VARCHAR(512) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);

CREATE TABLE audit_log (
	id VARCHAR(32) NOT NULL, 
	actor_id VARCHAR(32), 
	actor_email VARCHAR(255), 
	action VARCHAR(64) NOT NULL, 
	target VARCHAR(64), 
	before JSON NOT NULL, 
	after JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE balances (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	kind accountkind NOT NULL, 
	amount BIGINT NOT NULL, 
	locked BIGINT NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_balance_user_kind UNIQUE (user_id, kind)
);

CREATE TABLE bets (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	game VARCHAR(32) NOT NULL, 
	round_id VARCHAR(32), 
	stake BIGINT NOT NULL, 
	stake_source accountkind NOT NULL, 
	payout BIGINT NOT NULL, 
	multiplier NUMERIC(14, 6) NOT NULL, 
	profit BIGINT NOT NULL, 
	server_seed_hash VARCHAR(64), 
	client_seed VARCHAR(128), 
	nonce BIGINT, 
	params JSON NOT NULL, 
	result JSON NOT NULL, 
	settled BOOLEAN NOT NULL, 
	idempotency_key VARCHAR(128), 
	wager_contribution BIGINT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	settled_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (idempotency_key)
);

CREATE TABLE bonus_codes (
	id VARCHAR(32) NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	bonus_type VARCHAR(16) NOT NULL, 
	value NUMERIC(12, 2) NOT NULL, 
	max_amount BIGINT NOT NULL, 
	wager_multiplier NUMERIC(8, 2) NOT NULL, 
	uses_left INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	min_deposit BIGINT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE bonus_grants (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	code VARCHAR(32) NOT NULL, 
	amount BIGINT NOT NULL, 
	wager_required BIGINT NOT NULL, 
	wagered BIGINT NOT NULL, 
	max_cashout BIGINT, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	converted BOOLEAN NOT NULL, 
	source VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE chat_messages (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	body TEXT NOT NULL, 
	vip_tier INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE deposits (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	amount BIGINT NOT NULL, 
	credited BIGINT NOT NULL, 
	bonus_credited BIGINT NOT NULL, 
	fee BIGINT NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	method paymentmethod NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	provider_ref VARCHAR(128), 
	status depositstatus NOT NULL, 
	idempotency_key VARCHAR(128), 
	failure_reason VARCHAR(255), 
	card_last4 VARCHAR(4), 
	crypto_address VARCHAR(128), 
	crypto_txid VARCHAR(128), 
	confirmations INTEGER NOT NULL, 
	instructions JSON NOT NULL, 
	bonus_code VARCHAR(32), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (idempotency_key)
);

CREATE TABLE game_rounds (
	id VARCHAR(32) NOT NULL, 
	game VARCHAR(32) NOT NULL, 
	round_number BIGINT NOT NULL, 
	server_seed VARCHAR(128) NOT NULL, 
	server_seed_hash VARCHAR(64) NOT NULL, 
	client_seed VARCHAR(128) NOT NULL, 
	nonce BIGINT NOT NULL, 
	crash_point NUMERIC(12, 4) NOT NULL, 
	status roundstatus NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	crashed_at TIMESTAMP WITH TIME ZONE, 
	settled_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE jackpot_pools (
	id VARCHAR(32) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	amount BIGINT NOT NULL, 
	contribution_pct NUMERIC(6, 4) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE kyc_documents (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	doc_type VARCHAR(32) NOT NULL, 
	file_ref VARCHAR(255) NOT NULL, 
	status kycstatus NOT NULL, 
	reviewer_note TEXT, 
	reviewed_by VARCHAR(32), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE ledger_entries (
	id VARCHAR(32) NOT NULL, 
	transaction_id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32), 
	kind accountkind NOT NULL, 
	amount BIGINT NOT NULL, 
	balance_after BIGINT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(transaction_id) REFERENCES ledger_transactions (id) ON DELETE CASCADE
);

CREATE TABLE ledger_transactions (
	id VARCHAR(32) NOT NULL, 
	type txtype NOT NULL, 
	status txstatus NOT NULL, 
	user_id VARCHAR(32), 
	reference VARCHAR(128), 
	idempotency_key VARCHAR(128), 
	memo TEXT, 
	meta JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (idempotency_key)
);

CREATE TABLE limit_usage (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	day VARCHAR(10) NOT NULL, 
	deposit_total BIGINT NOT NULL, 
	loss_total BIGINT NOT NULL, 
	wager_total BIGINT NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_limit_user_day UNIQUE (user_id, day), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE notifications (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	title VARCHAR(128) NOT NULL, 
	body TEXT NOT NULL, 
	read BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE payment_webhooks (
	id VARCHAR(32) NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	event_id VARCHAR(128), 
	event_type VARCHAR(64) NOT NULL, 
	payload JSON NOT NULL, 
	signature_valid BOOLEAN NOT NULL, 
	processed BOOLEAN NOT NULL, 
	error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_id)
);

CREATE TABLE refresh_tokens (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	token_hash VARCHAR(128) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE session_audit (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32), 
	action VARCHAR(64) NOT NULL, 
	ip VARCHAR(64), 
	user_agent VARCHAR(255), 
	meta JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE users (
	id VARCHAR(32) NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	role userrole NOT NULL, 
	display_currency VARCHAR(3) NOT NULL, 
	country VARCHAR(2) NOT NULL, 
	date_of_birth TIMESTAMP WITH TIME ZONE, 
	phone VARCHAR(32), 
	kyc_status kycstatus NOT NULL, 
	kyc_full_name VARCHAR(255), 
	kyc_document_ref VARCHAR(255), 
	self_excluded_until TIMESTAMP WITH TIME ZONE, 
	cool_off_until TIMESTAMP WITH TIME ZONE, 
	loss_limit_daily BIGINT, 
	deposit_limit_daily BIGINT, 
	client_seed VARCHAR(128) NOT NULL, 
	server_seed VARCHAR(128) NOT NULL, 
	server_seed_hash VARCHAR(64) NOT NULL, 
	nonce BIGINT NOT NULL, 
	prev_server_seeds JSON NOT NULL, 
	vip_tier INTEGER NOT NULL, 
	wagered_lifetime BIGINT NOT NULL, 
	rakeback_claimed BIGINT NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	is_banned BOOLEAN NOT NULL, 
	email_verified BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE withdrawals (
	id VARCHAR(32) NOT NULL, 
	user_id VARCHAR(32) NOT NULL, 
	amount BIGINT NOT NULL, 
	fee BIGINT NOT NULL, 
	net_amount BIGINT NOT NULL, 
	method paymentmethod NOT NULL, 
	destination VARCHAR(255) NOT NULL, 
	provider VARCHAR(32) NOT NULL, 
	provider_ref VARCHAR(128), 
	payout_ref VARCHAR(191), 
	status withdrawalstatus NOT NULL, 
	idempotency_key VARCHAR(128), 
	reviewed_by VARCHAR(32), 
	review_note TEXT, 
	rejection_reason VARCHAR(255), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	paid_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (idempotency_key)
);

CREATE INDEX ix_audit_log_action ON audit_log (action);

CREATE INDEX ix_audit_log_actor_id ON audit_log (actor_id);

CREATE INDEX ix_balances_kind ON balances (kind);

CREATE INDEX ix_balances_user_id ON balances (user_id);

CREATE INDEX ix_bets_created_at ON bets (created_at);

CREATE INDEX ix_bets_game ON bets (game);

CREATE INDEX ix_bets_round_id ON bets (round_id);

CREATE INDEX ix_bets_settled ON bets (settled);

CREATE INDEX ix_bets_user_id ON bets (user_id);

CREATE INDEX ix_bonus_grants_code ON bonus_grants (code);

CREATE INDEX ix_bonus_grants_user_id ON bonus_grants (user_id);

CREATE INDEX ix_chat_messages_created_at ON chat_messages (created_at);

CREATE INDEX ix_chat_messages_user_id ON chat_messages (user_id);

CREATE INDEX ix_deposits_provider_ref ON deposits (provider_ref);

CREATE INDEX ix_deposits_status ON deposits (status);

CREATE INDEX ix_deposits_user_id ON deposits (user_id);

CREATE INDEX ix_game_rounds_game ON game_rounds (game);

CREATE INDEX ix_game_rounds_round_number ON game_rounds (round_number);

CREATE INDEX ix_game_rounds_status ON game_rounds (status);

CREATE INDEX ix_kyc_documents_user_id ON kyc_documents (user_id);

CREATE INDEX ix_ledger_entries_kind ON ledger_entries (kind);

CREATE INDEX ix_ledger_entries_transaction_id ON ledger_entries (transaction_id);

CREATE INDEX ix_ledger_entries_user_id ON ledger_entries (user_id);

CREATE INDEX ix_ledger_entries_user_kind ON ledger_entries (user_id, kind);

CREATE INDEX ix_ledger_transactions_created_at ON ledger_transactions (created_at);

CREATE INDEX ix_ledger_transactions_reference ON ledger_transactions (reference);

CREATE INDEX ix_ledger_transactions_type ON ledger_transactions (type);

CREATE INDEX ix_ledger_transactions_user_id ON ledger_transactions (user_id);

CREATE INDEX ix_limit_usage_day ON limit_usage (day);

CREATE INDEX ix_limit_usage_user_id ON limit_usage (user_id);

CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE INDEX ix_payment_webhooks_provider ON payment_webhooks (provider);

CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE INDEX ix_session_audit_user_id ON session_audit (user_id);

CREATE INDEX ix_withdrawals_provider_ref ON withdrawals (provider_ref);

CREATE INDEX ix_withdrawals_status ON withdrawals (status);

CREATE INDEX ix_withdrawals_user_id ON withdrawals (user_id);

CREATE UNIQUE INDEX ix_bonus_codes_code ON bonus_codes (code);

CREATE UNIQUE INDEX ix_refresh_tokens_token_hash ON refresh_tokens (token_hash);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE UNIQUE INDEX ix_users_username ON users (username);
