.PHONY: help install dev test run-api run-worker up up-scale down logs build clean tunnel tunnel-quick tunnel-url

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

tunnel-quick: ## Expose the API via an ephemeral Cloudflare quick tunnel
	docker compose --profile tunnel-quick up -d cloudflared-quick

tunnel-url: ## Print the current *.trycloudflare.com URL
	@docker compose logs cloudflared-quick 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || echo "no quick tunnel running"

tunnel: ## Run the named Cloudflare tunnel (needs CLOUDFLARE_TUNNEL_TOKEN)
	docker compose --profile tunnel up -d cloudflared

clean: ## Remove local data and caches
	rm -rf data .pytest_cache __pycache__ */__pycache__
