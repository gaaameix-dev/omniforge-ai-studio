.PHONY: install test lint format run serve eval doctor history docker clean

install:
	pip install -e ".[dev]"

test:
	python -m pytest -q

lint:
	ruff check .

format:
	ruff check --fix . && ruff format .

run:
	omniforge run "$(GOAL)"

serve:
	omniforge serve --host 0.0.0.0 --port 8000

eval:
	omniforge eval

doctor:
	omniforge doctor

history:
	omniforge history --limit 20

docker:
	docker compose up --build

clean:
	rm -rf data .pytest_cache .ruff_cache build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
