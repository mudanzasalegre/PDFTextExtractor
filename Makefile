.PHONY: run test docker-up docker-down

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -q

docker-up:
	docker compose up --build

docker-down:
	docker compose down
