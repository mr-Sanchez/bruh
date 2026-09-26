# Voice Practice Coach — start / stop the local server.
# Needs GNU make (Windows: `choco install make`) and Git Bash's sh on PATH.

PORT   ?= 8420
VENV   := .venv/Scripts/activate
ACTIVATE := . $(VENV)
PS     := powershell -NoProfile -Command

.PHONY: help run start stop restart status test selftest install

help:
	@echo "make run       - start in this terminal (Ctrl+C stops it)"
	@echo "make start     - start in the background (hidden window) and open the browser"
	@echo "make stop      - stop the server listening on port $(PORT)"
	@echo "make restart   - stop, then start"
	@echo "make status    - is the server up?"
	@echo "make test      - run the offline test suite"
	@echo "make selftest  - offline smoke check"
	@echo "make install   - pip install -r requirements.txt into .venv"

run:
	$(ACTIVATE) && APP_PORT=$(PORT) python -m app.main

start:
	@$(PS) "if (Get-NetTCPConnection -LocalPort $(PORT) -State Listen -ErrorAction SilentlyContinue) { Write-Host 'Already running on port $(PORT)'; exit 0 }; \
	  $$env:APP_PORT='$(PORT)'; \
	  Start-Process -WindowStyle Hidden -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','app.main'; \
	  Write-Host 'Started: http://127.0.0.1:$(PORT)'"

stop:
	@$(PS) "$$c = Get-NetTCPConnection -LocalPort $(PORT) -State Listen -ErrorAction SilentlyContinue; \
	  if ($$c) { $$c | ForEach-Object { Stop-Process -Id $$_.OwningProcess -Force }; Write-Host 'Stopped' } \
	  else { Write-Host 'Not running' }"

restart: stop start

status:
	@$(PS) "if (Get-NetTCPConnection -LocalPort $(PORT) -State Listen -ErrorAction SilentlyContinue) { Write-Host 'Running: http://127.0.0.1:$(PORT)' } else { Write-Host 'Stopped' }"

test:
	$(ACTIVATE) && python -m unittest discover -s tests -v

selftest:
	$(ACTIVATE) && python -m app.main --selftest

install:
	$(ACTIVATE) && pip install -r requirements.txt
