# Presenton sidecar (issue #350)

[Presenton](https://presenton.ai/) is an Apache 2.0 AI presentation generator. Chad calls it via `generate_presentation` when `visual.presenton_url` points at this container.

## Quick start

1. Copy the env template and add your LLM provider key:

```powershell
Copy-Item docker/presenton/.env.example docker/presenton/.env
# Edit docker/presenton/.env — at minimum set LLM and the matching API key
```

2. Start the optional profile:

```powershell
docker compose -f docker/chad-services.yml --profile presenton up -d
```

3. Point Chad at the instance (`config/config.yaml`):

```yaml
visual:
  presenton_url: http://127.0.0.1:5000
```

4. Verify:

```powershell
.\scripts\setup\verify_chad_services.ps1 -PresentonUrl http://127.0.0.1:5000
```

Open http://127.0.0.1:5000 to finish provider setup in the UI if needed.

## LLM providers

Presenton needs an LLM backend. Common choices (set in `docker/presenton/.env`):

| `LLM` value | Keys / notes |
|-------------|--------------|
| `google` | `GOOGLE_API_KEY` — simplest for first run |
| `openai` | `OPENAI_API_KEY` |
| `ollama` | Local models; often needs `PEXELS_API_KEY` for stock images |

Set `CAN_CHANGE_KEYS=false` in production so keys stay in the env file, not the UI.

## API modes

| Mode | Config | Endpoint |
|------|--------|----------|
| **Sync (default)** | `visual.presenton_use_async: false` | `POST /api/v1/ppt/presentation/generate` |
| **Async poll** | `visual.presenton_use_async: true` | `POST /api/v3/presentation/generate/async` + status poll |

Self-hosted Docker typically uses **sync v1** without an API key. Cloud / API-key deployments can enable async v3 and set `PRESENTON_API_KEY` in `config/secrets.yaml`.

## Data volume

Presentation files persist in the `presenton-data` Docker volume (`/app/app_data` in the container).

## Standalone run (without compose)

```powershell
docker run -it --name presenton -p 5000:80 `
  -e LLM="google" -e GOOGLE_API_KEY="your-key" -e CAN_CHANGE_KEYS="false" `
  -v "${PWD}/docker/presenton/user_data:/app/app_data" `
  ghcr.io/presenton/presenton:latest
```
