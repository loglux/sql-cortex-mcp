.PHONY: build up down restart logs lint test check

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

restart: build up

logs:
	docker compose logs -f

lint:
	.venv/bin/ruff check .
	.venv/bin/black --check .
	npx stylelint "app/web/static/**/*.css"
	.venv/bin/djlint app/web/templates --lint

test:
	.venv/bin/pytest --tb=short -q

check: lint test
