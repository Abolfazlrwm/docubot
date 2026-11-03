# Common development tasks. Requires docker compose with the dev stack running.
#   cp .env.example .env && make up && make migrate

.DEFAULT_GOAL := help
COMPOSE = docker compose

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Build and start the dev stack (web, worker, db, redis)
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop the dev stack
	$(COMPOSE) down

.PHONY: migrate
migrate: ## Apply database migrations
	$(COMPOSE) exec web python manage.py migrate

.PHONY: superuser
superuser: ## Create a Django admin user
	$(COMPOSE) exec web python manage.py createsuperuser

.PHONY: test
test: ## Run the test suite with coverage (fails under 85%)
	$(COMPOSE) exec web pytest

.PHONY: lint
lint: ## Ruff lint + format check + migration check
	$(COMPOSE) exec web ruff check .
	$(COMPOSE) exec web ruff format --check .
	$(COMPOSE) exec web python manage.py makemigrations --check

.PHONY: format
format: ## Auto-format the codebase with ruff
	$(COMPOSE) exec web ruff format .
	$(COMPOSE) exec web ruff check --fix .

.PHONY: shell
shell: ## Django shell inside the web container
	$(COMPOSE) exec web python manage.py shell

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

.PHONY: prod-up
prod-up: ## Start the production-like stack
	$(COMPOSE) -f docker-compose.prod.yml up -d --build
