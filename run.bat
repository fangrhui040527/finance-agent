@echo off
REM FinPlanet Module 5 - Windows commands.
REM
REM The Makefile is Unix-only; this is the same surface for Windows. Run with no
REM argument to see what is available.
REM
REM   run install     first time setup
REM   run test        the full suite
REM   run lint        ruff check + format check
REM   run fmt         ruff autofix + format
REM   run typecheck   pyright
REM   run cov         tests with the coverage gate
REM   run verify      the whole pipeline on mock data, no network, no keys
REM   run config      show settings and where they came from
REM   run why ...     decompose a move before naming a cause
REM   run plan ...    what the system would do with a question
REM   run log ...     log a view before you find out
REM   run trace       full traced system run -> debug\<run_id>\
REM   run graph       build the knowledge graph -> data\graph.db
REM   run codegraph   build the repo graph -> data\codegraph.db
REM   run graph-report  hubs, orphans, review queue, surprises
REM   run mcp         serve MCP on stdio (Claude Desktop / Claude Code)
REM   run mcp-check   MCP handshake selftest, no client needed
REM   run due         what has reached its horizon
REM   run grade ...   score a call
REM   run status      the calibration table
REM   run up/down/health   the datastores, via Docker

setlocal
set "PY=.venv\Scripts\python.exe"
set "CMD=%~1"
if "%CMD%"=="" goto usage

REM Everything except install needs the venv.
if /I not "%CMD%"=="install" (
  if not exist "%PY%" (
    echo No virtual environment found at %PY%.
    echo Run:  run install
    exit /b 1
  )
)

REM Shift the subcommand off so %* is just the arguments.
shift
set "ARGS="
:collect
if "%~1"=="" goto dispatch
set "ARGS=%ARGS% %1"
shift
goto collect

:dispatch
if /I "%CMD%"=="install" goto install
if /I "%CMD%"=="test"    goto test
if /I "%CMD%"=="lint"    goto lint
if /I "%CMD%"=="fmt"     goto fmt
if /I "%CMD%"=="typecheck" goto typecheck
if /I "%CMD%"=="cov"     goto cov
if /I "%CMD%"=="verify"  goto verify
if /I "%CMD%"=="stress"  goto stress
if /I "%CMD%"=="config"  goto config
if /I "%CMD%"=="why"     goto why
if /I "%CMD%"=="plan"    goto plan
if /I "%CMD%"=="log"     goto log
if /I "%CMD%"=="trace"   goto trace
if /I "%CMD%"=="graph"   goto graph
if /I "%CMD%"=="codegraph" goto codegraph
if /I "%CMD%"=="graph-report" goto graphreport
if /I "%CMD%"=="mcp"     goto mcp
if /I "%CMD%"=="mcp-check" goto mcpcheck
if /I "%CMD%"=="due"     goto due
if /I "%CMD%"=="grade"   goto grade
if /I "%CMD%"=="status"  goto status
if /I "%CMD%"=="up"      goto up
if /I "%CMD%"=="down"    goto down
if /I "%CMD%"=="health"  goto health
echo Unknown command: %CMD%
echo.
goto usage

:install
where uv >nul 2>&1
if errorlevel 1 (
  echo uv is not installed. Install it with:
  echo   powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
  exit /b 1
)
uv sync --frozen --python 3.11 || exit /b 1
echo.
echo Installed. Next:  run verify
goto :eof

:test
"%PY%" -m pytest
goto :eof

:lint
"%PY%" -m ruff check . && "%PY%" -m ruff format --check .
goto :eof

:fmt
"%PY%" -m ruff check --fix . && "%PY%" -m ruff format .
goto :eof

:typecheck
"%PY%" -m pyright
goto :eof

:cov
"%PY%" -m pytest --cov --cov-report=term-missing
goto :eof

:verify
"%PY%" verify.py
goto :eof

:stress
"%PY%" stress\run.py
goto :eof

:config
"%PY%" -c "from core.config import load; print(load().describe())"
goto :eof

:why
"%PY%" ask.py why%ARGS%
goto :eof

:plan
"%PY%" ask.py plan%ARGS%
goto :eof

:log
"%PY%" predict.py log%ARGS%
goto :eof

:trace
"%PY%" trace_run.py%ARGS%
goto end

:graph
"%PY%" -m knowledge.graph.build --prune%ARGS%
goto end

:codegraph
"%PY%" -m knowledge.graph.build --code --prune%ARGS%
goto end

:graphreport
"%PY%" ask.py graph --report%ARGS%
goto end

:mcp
"%PY%" -m mcp_server.server%ARGS%
goto end

:mcpcheck
"%PY%" -m mcp_server.server --selftest
goto end

:due
"%PY%" predict.py due%ARGS%
goto :eof

:grade
"%PY%" predict.py grade%ARGS%
goto :eof

:status
"%PY%" predict.py status%ARGS%
goto :eof

:up
docker compose -f infra\docker-compose.yml up -d
goto :eof

:down
docker compose -f infra\docker-compose.yml down
goto :eof

:health
docker compose -f infra\docker-compose.yml ps
goto :eof

:usage
echo FinPlanet Module 5 - The Analyst Mind
echo.
echo   run install                       create .venv and install
echo   run test                          the full suite
echo   run lint ^| fmt ^| typecheck ^| cov  quality gates
echo   run verify                        whole pipeline on mock data
echo   run stress                        adversarial stress suite
echo   run config                        settings, and where they came from
echo.
echo   run why MYX:1155 --move -0.09 --market -0.08
echo   run plan "why did maybank fall today" --instrument MYX:1155
echo.
echo   run log MYX:1155 +1 63d 0.62 "NIM stabilises above 2.25%%"
echo   run due                           what has reached its horizon
echo   run grade ID --return 0.031 --benchmark 0.048
echo   run status                        the calibration table
echo.
echo   run up ^| down ^| health            the datastores, via Docker
exit /b 1
