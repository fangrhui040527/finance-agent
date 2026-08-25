.PHONY: install test verify up down health lint due status
install: ; uv venv --python 3.11 .venv && . .venv/bin/activate && uv pip install -e ".[dev]"
test:    ; . .venv/bin/activate && python -m pytest
verify:  ; . .venv/bin/activate && python verify.py
due:     ; . .venv/bin/activate && python predict.py due
status:  ; . .venv/bin/activate && python predict.py status
up:      ; docker compose -f infra/docker-compose.yml up -d
down:    ; docker compose -f infra/docker-compose.yml down
health:  ; docker compose -f infra/docker-compose.yml ps
