.PHONY: help install dev test run-api run-worker up down logs build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	pip install -r requirements.txt

dev: ## Install dev/test dependencies
	pip install -r requirements-dev.txt

test: ## Run the test suite
	pytest

run-api: ## Run the API locally (needs a local Redis)
	REDIS_HOST=localhost uvicorn upscale_api.api:app --reload --port 8000

run-worker: ## Run a worker locally (needs Redis + ML deps installed)
	REDIS_HOST=localhost arq upscale_api.worker.WorkerSettings

build: ## Build all Docker images
	docker compose build

up: ## Start the full stack (redis + api + worker)
	docker compose up -d

up-scale: ## Start with 3 worker instances
	docker compose up -d --scale worker=3

down: ## Stop the stack
	docker compose down

logs: ## Tail service logs
	docker compose logs -f

clean: ## Remove local data and caches
	rm -rf data .pytest_cache __pycache__ */__pycache__
