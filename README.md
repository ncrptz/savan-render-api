# SAVAN Certificate Render API

FastAPI microservice — deployed on Railway — called by the SAVAN Next.js portal.

## Files to commit to GitHub
- Dockerfile
- main.py
- requirements.txt
- railway.json
- .gitignore
- README.md

## Files NOT in git (too large / sensitive)
- persistent_assets.json  ← upload manually to Railway Volume

## Deploy steps (done once)

1. Push this folder to a new GitHub repo (e.g. savan-render-api)
2. Go to railway.app → New Project → Deploy from GitHub repo
3. Select your repo → Railway detects Dockerfile automatically
4. Wait ~3 min for build (installs Cairo + Python packages)
5. Go to your service → Settings → Volumes
   → Add Volume  mount path: /app
   → Upload persistent_assets.json to that volume
6. Your service URL looks like: https://savan-render-api.up.railway.app
7. Copy that URL → paste into Vercel as RENDER_API_URL

## Test it
curl https://your-service.up.railway.app/health
# → {"status":"ok","service":"savan-render"}
