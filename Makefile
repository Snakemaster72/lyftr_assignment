SHELL := /bin/sh
BASE_URL ?= http://localhost:8000
COMPOSE := docker compose
PYTHON_VENV := backend/.venv/bin

.DEFAULT_GOAL := help
.PHONY: help up down logs ps test test-local verify clean env

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

env:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "WARNING: .env was missing, created it from .env.example."; \
		echo "         Put real keys in LLM_API_KEYS, or chat requests will return 503."; \
	fi

up: env ## Build and start backend (:8000) and frontend (:3000)
	$(COMPOSE) up -d --build
	@echo "Frontend: http://localhost:3000"
	@echo "Backend:  http://localhost:8000/health"

down: ## Stop the stack (keeps the data volume)
	$(COMPOSE) down

logs: ## Follow logs
	$(COMPOSE) logs -f

ps: ## Show service status
	$(COMPOSE) ps

test: ## Run backend tests inside the backend runtime image
	docker build --target test -t lyftr-chatbot-backend:test backend
	docker run --rm lyftr-chatbot-backend:test

test-local: ## Run backend + frontend tests on the host (needs backend/.venv and npm i)
	cd backend && .venv/bin/ruff check . && .venv/bin/pytest
	cd frontend && npm test

verify: ## Run scripts/verify.sh against BASE_URL (default http://localhost:8000)
	BASE_URL=$(BASE_URL) ./scripts/verify.sh

clean: ## Stop the stack and DELETE the data volume (chat history + cache)
	@echo "WARNING: this deletes the app-data volume (all chat history)."
	$(COMPOSE) down -v --remove-orphans
