# Deploying SuwaPath

SuwaPath runs self-hosted on a single VPS (`159.65.1.78`,
`suwapath.pasidumihiranga.me`) behind Caddy, deployed automatically by GitHub
Actions on every push to `main`. That's the live path — see
[Self-hosted VPS](#self-hosted-vps-production) below. A free-tier split
hosting alternative (Vercel + Hugging Face Spaces + Neon) is documented
afterward for anyone who wants a no-VPS option.

---

## Self-hosted VPS (production)

One Docker Compose stack, one reverse proxy:

```
Internet ──443/80──▶ Caddy ──┬─▶ /                          → static frontend build
                              └─▶ /api/*, /docs, /health, …  → backend (internal only)
                     backend ──▶ postgres, qdrant (internal only)
```

Same-origin (frontend and API share one domain via Caddy path routing), so
there is no CORS in production — the frontend is built with
`VITE_API_BASE=""`. Caddy issues and renews its own Let's Encrypt certificate
automatically; nothing else touches TLS. Only Caddy publishes ports 80/443 —
Postgres, Qdrant and the backend are reachable only on the compose-internal
network.

### Files

- [`deploy/docker-compose.yml`](deploy/docker-compose.yml) — the production
  stack (postgres, qdrant, backend, caddy). Lives at
  `/opt/suwapath/docker-compose.yml` on the server.
- [`deploy/Caddyfile`](deploy/Caddyfile) — path-based routing and TLS.
- [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) — builds the
  backend image, pushes it to GHCR, builds the frontend, and deploys both to
  the VPS over SSH on every push to `main` (or via **Run workflow** in the
  Actions tab).

### One-time server provisioning (already done for this deployment)

1. Install Docker Engine + Compose plugin.
2. Create a non-root `deploy` user in the `docker` group, with its own
   dedicated SSH keypair (not the admin's personal key) — its public half in
   `authorized_keys`, its private half stored only as the `VPS_SSH_KEY`
   GitHub secret.
3. `ufw allow OpenSSH,80,443` and enable.
4. `mkdir -p /opt/suwapath`, owned by `deploy`.
5. Write `/opt/suwapath/.env` once, by hand, with freshly generated
   `POSTGRES_PASSWORD`, `JWT_SECRET` and `SUWAPATH_ENCRYPTION_KEY` (same
   one-liners as [Configuration](#configuration) below) plus
   `DATABASE_URL=postgresql+psycopg2://suwapath:<pg-password>@postgres:5432/suwapath`
   and `QDRANT_URL=http://qdrant:6333`. This file is **never** touched by CI
   or committed — losing it is exactly as unrecoverable as losing it
   anywhere else.

### GitHub repo secrets

| Secret | Value |
|---|---|
| `VPS_HOST` | `159.65.1.78` |
| `VPS_SSH_USER` | `deploy` |
| `VPS_SSH_KEY` | private half of the `deploy` user's dedicated keypair |

No registry credential is stored on the server — the deploy job logs the VPS
into GHCR with its own short-lived `GITHUB_TOKEN` over SSH, on every run.

### First deploy / re-seeding

```bash
ssh deploy@159.65.1.78
cd /opt/suwapath
docker compose exec backend python -m app.seed.seeder --reset   # once
```

`--reset` truncates every table — safe on an empty database, destructive on a
live one. Run it once, not on every deploy.

### Checks

- `curl -s https://suwapath.pasidumihiranga.me/health` → `"status":"ok"`,
  `"database":"connected"`, valid cert.
- Hard-refresh a deep link like `/patient/appointments` — no 404 (Caddy's
  `try_files` fallback to `index.html`).
- `docker compose logs caddy` on the server shows ACME issuance succeeded.

---

## Alternative: free-tier split hosting

The deployment is split in two, because the two halves have opposite needs.

- **Frontend** — a static SPA on **Vercel**, at `suwapath.pasidumihiranga.me`.
  Instant, always awake, free custom domain with automatic TLS.
- **API** — a Docker container on **Hugging Face Spaces**. It carries
  onnxruntime, a MiniLM embedding model, Tesseract and the CV adapter, needs
  1–2 GB of RAM, and is the only free tier that gives it comfortably (16 GB).
- **Database** — **Neon** Postgres, free and permanent. All demo data lives
  here, so it survives every container rebuild.

The submitted URL is the Vercel one. That matters: Vercel supports custom
domains on the free plan, so **the address is yours**. If the API ever moves,
or the whole app moves, you change a build variable or a DNS record and the
submitted link keeps working. Own the name, not the host.

### 1. Database — Neon (5 min)

1. Sign up at neon.tech (no card), create a project in the region nearest you.
2. Copy the **pooled** connection string. It looks like
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require`.
3. Convert the scheme for SQLAlchemy — `postgresql://` → `postgresql+psycopg2://`.

Neon scales to zero when idle and wakes on connection. `pool_pre_ping` is
already enabled in `app/core/db.py`, which is what makes that safe: a stale
pooled connection is detected and replaced instead of erroring.

### 2. Seed the database, once, from your laptop

```bash
cd backend
export DATABASE_URL='postgresql+psycopg2://...neon.../neondb?sslmode=require'
.venv/bin/python -m app.seed.seeder --reset
```

Run this **exactly once**. `--reset` truncates 35 tables, which is correct on
an empty database and destructive on a live one. The knowledge index does not
need a separate step — the API rebuilds it on boot.

Generate the two secrets you will need next:

```bash
python3 -c "import secrets;print('JWT_SECRET=' + secrets.token_urlsafe(48))"
python3 -c "import os,base64;print('SUWAPATH_ENCRYPTION_KEY=' + base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Keep the encryption key safe. Losing it makes every stored conversation
unreadable — that is the point of it, and it has no recovery path.

### 3. API — Hugging Face Space (10 min)

1. huggingface.co → **New Space** → SDK **Docker**, hardware **CPU basic
   (free)**, visibility **public**.
2. **Settings → Variables and secrets**, add:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | the Neon string from step 1 |
   | `JWT_SECRET` | generated above |
   | `SUWAPATH_ENCRYPTION_KEY` | generated above |
   | `EXTRA_CORS_ORIGINS` | `https://suwapath.pasidumihiranga.me` |
   | `ENVIRONMENT` | `production` |
   | `GROQ_API_KEY` | optional — without it, answers use the deterministic composers |

3. Push this repository to the Space:

   ```bash
   git remote add space https://huggingface.co/spaces/<user>/<space>
   git push space main
   ```

   HF builds the image from `Dockerfile`. First build takes 5–10 minutes.

4. Check it: `https://<user>-<space>.hf.space/health` should return
   `"status": "ok"` with `"database": "connected"`.

The container is deliberately single-worker: the scheduler runs in-process and
the embedded vector index takes an exclusive directory lock. Both would break
under multiple workers, and neither matters at demo traffic.

### 4. Frontend — Vercel (10 min)

1. vercel.com → **Add New Project** → import the GitHub repo.
2. **Root Directory: `frontend`** — this is the setting people miss, and
   without it the build fails looking for a package.json at the repo root.
3. Environment variable: `VITE_API_BASE` = `https://<user>-<space>.hf.space`
   (no trailing slash). This is read at **build time**, so changing it later
   needs a redeploy, not just a restart.
4. Deploy. `frontend/vercel.json` already handles SPA routing, so a hard
   refresh on `/patient/appointments` serves the app instead of a 404.

### 5. The domain

In Vercel → **Settings → Domains**, add `suwapath.pasidumihiranga.me`. Vercel
shows one CNAME record. Your teammate adds it at the registrar for the `.me`
domain; TLS is issued automatically once it resolves.

Then come back and update `EXTRA_CORS_ORIGINS` on the Space if the final
hostname differs from what you set in step 3 — the browser will block every
API call from an origin the API does not list.

---

### Checks before you call it done

- `https://suwapath.pasidumihiranga.me` loads over HTTPS.
- Each demo button on the login page fills the form, and signing in lands on a
  **populated** dashboard — appointments, recommendations, care programmes.
- The Assistant replies. Plain wording means no LLM key is set, which is a
  supported mode rather than a failure.
- Medical Reports and Image Screening render their images. If those 404 or
  401, `VITE_API_BASE` or `EXTRA_CORS_ORIGINS` is wrong.
- Hard-refresh a deep link such as `/patient/appointments`.
- Open the browser console — no CORS errors.

### Known limits, worth stating plainly

**Uploaded files do not survive a rebuild.** The container filesystem is
ephemeral; seeded data is in Postgres and is safe, but a document a reviewer
uploads is gone after the next deploy.

**The Space sleeps after about 48 hours idle** and takes roughly 30 seconds to
wake on the next request. The URL never changes. The frontend is always awake,
so a sleeping API shows as a slow first load rather than a dead site.

**The chest X-ray model is the untrained baseline.** BioFusion's weights were
never committed to git, so there is nothing to ship yet; the adapter reports
`is_trained_model: false` and the UI labels it as a baseline. Drop a trained
`.onnx` into `models/pneumonia/` and the ONNX adapter takes over on restart.

**All data is synthetic.** No real patient information is present, which is
what makes publishing demo credentials acceptable.
