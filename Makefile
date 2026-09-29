# Naktsang Casino - developer entry points.
#
# `make install` then `make api` and `make web` in two terminals gets you a
# running casino with the sandbox payment provider (no real money moves).

PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
PORT ?= 8000
WEB_PORT ?= 5173
BASE ?= http://127.0.0.1:$(PORT)

.PHONY: help install api web build test smoke smoke-serverless deploy-check schema-check reset fmt clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## create the venv and install backend + frontend dependencies
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r backend/requirements.txt
	cd frontend && npm install

api: ## run the API on :$(PORT) (reloads on save)
	cd backend && ../$(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port $(PORT)

web: ## run the frontend dev server on :$(WEB_PORT)
	cd frontend && npm run dev -- --port $(WEB_PORT)

build: ## production build, copied into the function bundle as the deploy does
	cd frontend && npm run build \
		&& rm -rf ../backend/static && mkdir -p ../backend/static \
		&& cp -R dist/. ../backend/static/

test: ## run the whole backend test suite
	cd backend && PYTHONPATH=. ../$(PY) -m pytest tests/ -q

smoke: ## drive the live API end to end (deposit -> play -> withdraw -> payout)
	cd backend && PYTHONPATH=. ../$(PY) scripts/smoke_e2e.py $(BASE)

smoke-serverless: ## run the app the way Vercel does (no lifespan, no loops, /tmp db)
	cd backend && PYTHONPATH=. ../$(PY) scripts/smoke_serverless.py

deploy-check: ## verify the Vercel configuration before deploying
	$(PY) -c "import json;json.load(open('vercel.json'));print('vercel.json is valid JSON')"
	cd backend && PYTHONPATH=. ../$(PY) -m pytest tests/test_deploy_config.py -q
	cd backend && PYTHONPATH=. ../$(PY) scripts/smoke_serverless.py

schema-check: ## validate vercel.json against Vercel's own schema (needs vercel)
	cd frontend && node scripts/validate-vercel-config.mjs

reset: ## delete the local database (DESTROYS local player balances)
	rm -f backend/data/casino.db*

clean: ## remove caches and build output
	rm -rf frontend/dist .pytest_cache backend/**/__pycache__
