.PHONY: install test verify stress doctor watch capital web config up down health lint fmt typecheck cov due status mcp mcp-check trace graph codegraph graph-report
install: ; uv sync --frozen --python 3.11
lint:    ; . .venv/bin/activate && ruff check . && ruff format --check .
fmt:     ; . .venv/bin/activate && ruff check --fix . && ruff format .
typecheck: ; . .venv/bin/activate && pyright
doctor:  ; . .venv/bin/activate && python ask.py doctor
capital: ; . .venv/bin/activate && python ask.py capital
allocate: ; . .venv/bin/activate && python ask.py allocate
watch:   ; . .venv/bin/activate && python ask.py watch
web:     ; . .venv/bin/activate && python -m web.serve
cov:     ; . .venv/bin/activate && python -m pytest --cov --cov-report=term-missing
test:    ; . .venv/bin/activate && python -m pytest
verify:  ; . .venv/bin/activate && python verify.py
stress:  ; . .venv/bin/activate && python stress/run.py
config:  ; . .venv/bin/activate && python -c "from core.config import load; print(load().describe())"
trace:   ; . .venv/bin/activate && python trace_run.py
graph:   ; . .venv/bin/activate && python -m knowledge.graph.build --prune
codegraph: ; . .venv/bin/activate && python -m knowledge.graph.build --code --prune
graph-report: ; . .venv/bin/activate && python ask.py graph --report
mcp:     ; . .venv/bin/activate && python -m mcp_server.server
mcp-check: ; . .venv/bin/activate && python -m mcp_server.server --selftest
due:     ; . .venv/bin/activate && python predict.py due
status:  ; . .venv/bin/activate && python predict.py status
up:      ; docker compose -f infra/docker-compose.yml up -d
down:    ; docker compose -f infra/docker-compose.yml down
health:  ; docker compose -f infra/docker-compose.yml ps
