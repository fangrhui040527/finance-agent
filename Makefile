.PHONY: install test verify stress config up down health lint due status mcp mcp-check
install: ; uv venv --python 3.11 .venv && . .venv/bin/activate && uv pip install -e ".[dev]"
test:    ; . .venv/bin/activate && python -m pytest
verify:  ; . .venv/bin/activate && python verify.py
stress:  ; . .venv/bin/activate && python stress/run.py
config:  ; . .venv/bin/activate && python -c "from core.config import load; print(load().describe())"
mcp:     ; . .venv/bin/activate && python -m mcp_server.server
mcp-check: ; . .venv/bin/activate && python -m mcp_server.server --selftest
due:     ; . .venv/bin/activate && python predict.py due
status:  ; . .venv/bin/activate && python predict.py status
up:      ; docker compose -f infra/docker-compose.yml up -d
down:    ; docker compose -f infra/docker-compose.yml down
health:  ; docker compose -f infra/docker-compose.yml ps
