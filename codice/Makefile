.PHONY: up down logs migrate seed shell-backend shell-db reset

# ── Docker ────────────────────────────────────────────────────────────────────
up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f backend worker

# ── DB ────────────────────────────────────────────────────────────────────────
migrate:
	docker compose exec backend alembic upgrade head

seed:
	docker compose exec backend python scripts/seed_competitions.py

# ── Shells ────────────────────────────────────────────────────────────────────
shell-backend:
	docker compose exec backend bash

shell-db:
	docker compose exec db psql -U $${POSTGRES_USER} -d $${POSTGRES_DB}

# ── First-run sequence ────────────────────────────────────────────────────────
bootstrap: up
	@echo "Waiting for services…"; sleep 8
	$(MAKE) migrate
	$(MAKE) seed
	@echo ""
	@echo "Done — open http://localhost:3000"
	@echo "Login: $$(grep ADMIN_EMAIL .env | cut -d= -f2) / $$(grep ADMIN_PASSWORD .env | cut -d= -f2)"

# ── Reset (DESTRUCTIVE) ───────────────────────────────────────────────────────
reset:
	docker compose down -v
	docker compose up -d --build
	@sleep 8
	$(MAKE) migrate
	$(MAKE) seed
