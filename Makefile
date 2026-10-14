.PHONY: setup executor start dev test test-backend test-frontend lint e2e clean-dados

setup:            ## instala dependências, compila o frontend e constrói o executor isolado
	./scripts/setup.sh

executor:         ## (re)constrói a imagem Docker do executor isolado
	./scripts/build-executor.sh

start:            ## inicia a aplicação em http://127.0.0.1:8000
	./scripts/start.sh

dev:              ## API com recarga automática (8000) + Vite (5173); abra http://localhost:5173
	cd backend && .venv/bin/python -m uvicorn app.main:criar_app --factory --reload --host 127.0.0.1 --port 8000 & \
	cd frontend && npm run dev

test: test-backend test-frontend

test-backend:     ## testes do backend (os que usam Docker são pulados se ele não estiver disponível)
	cd backend && .venv/bin/python -m pytest -q

test-frontend:    ## typecheck + testes unitários do frontend
	cd frontend && npm run typecheck && npm test

lint:             ## ruff + pyright no backend, ESLint no frontend
	cd backend && .venv/bin/ruff check app tests ../executor && .venv/bin/pyright
	cd frontend && npm run lint

e2e:              ## testes no navegador (Playwright + axe); requer `npm run build` e o executor construído
	cd frontend && npm run build && npx playwright test

clean-dados:      ## apaga o banco local (projetos, blocos e histórico)
	rm -rf data
