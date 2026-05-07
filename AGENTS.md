# Repository Guidelines

## Project Structure & Module Organization

This repository is a mock MVP for a website-based CS2 demo AI coach.

- `frontend/`: Next.js + TypeScript app. App Router pages live in `frontend/app/`; shared UI is in `frontend/components/`; API helpers are in `frontend/lib/`; shared frontend types are in `frontend/types/`; static assets are in `frontend/public/`.
- `backend/`: FastAPI service and worker code. Routes are in `backend/app/api/`; SQLAlchemy models in `backend/app/models/`; Pydantic schemas in `backend/app/schemas/`; business logic in `backend/app/services/`; Redis worker entrypoint in `backend/app/workers/worker.py`.
- `docker-compose.yml`: local stack for `frontend`, `api`, `worker`, `postgres`, and `redis`.
- `README.md`: product scope, mock flow, API list, and next-phase parser/render notes.

## Build, Test, and Development Commands

- `docker compose up --build`: build and run the full local stack.
- `curl http://localhost:8000/health`: verify API, database, and Redis health.
- `cd frontend && npm run dev`: run the frontend dev server outside Docker.
- `cd frontend && npm run lint`: run ESLint with zero warnings allowed.
- `cd frontend && npm run typecheck`: run TypeScript checks without emitting files.
- `cd frontend && npm run build`: verify the production Next.js build.
- `python3 -m compileall backend/app`: quick backend syntax/import sanity check.

## Coding Style & Naming Conventions

Use TypeScript for frontend changes and Python 3.12 style for backend changes. Keep components in PascalCase (`FirstPersonReplay.tsx`) and hooks/helpers in camelCase. Backend modules use snake_case filenames and explicit service classes where existing patterns do. Prefer typed schemas over loose dictionaries at API boundaries. Keep comments short and only where they clarify non-obvious behavior.

## Testing Guidelines

There is no dedicated test suite yet. For every change, run the relevant verification commands above. For replay UI changes, manually verify `/dashboard` and a demo detail page: play/pause, seek, speed, round selection, coaching event click-to-seek, tactical map sync, and render status fallback.

## Commit & Pull Request Guidelines

This checkout does not include Git history, so use concise imperative commits such as `Add mock render job status API`. Pull requests should include: purpose, changed backend/frontend surfaces, verification commands run, screenshots for UI changes, and any known limitations.

## Security & Configuration Tips

Treat `.dem` and archive uploads as untrusted. Do not add real CS2 automation, OpenAI calls, OBS/ffmpeg capture, or object storage credentials to this mock phase unless the README scope changes. Keep secrets out of source and prefer environment variables in Compose or deployment config.
