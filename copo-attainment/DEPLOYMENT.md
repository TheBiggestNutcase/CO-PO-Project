# Deploying to Render (free tier)

This deploys the app as a Docker container (so the VTU scraper's Chrome +
Tesseract dependencies come along) with a free external Postgres database
for persistent storage, since Render's free web services don't keep a
local disk across restarts/redeploys.

**Heads up before you start:** the Dockerfile in this repo could not be
build-tested from the sandbox this was written in (no route to Docker Hub
from there), so Render's first build of it is the real first test. If it
fails, the error will be in Render's build logs - come back with that and
it's usually a one-line fix (a missing apt package, most likely).

## 1. Create a free Postgres database (Neon)

1. Go to [neon.com](https://neon.com) and sign up (no credit card needed).
2. Create a project. Any region is fine.
3. From the project dashboard, copy the **connection string** - it looks
   like `postgresql://user:password@ep-xxxx.region.aws.neon.tech/dbname?sslmode=require`.
   Keep this for step 3.

(Neon over Supabase deliberately: Neon's free database auto-resumes on
the next query after being idle. Supabase's free tier pauses after a
week of inactivity and needs a human to manually un-pause it from their
dashboard - a bad fit for a tool that might sit unused between exam
cycles.)

## 2. Push this to GitHub

Merge the `feature/vtu-scraper-exit-survey` branch (or whichever branch
has these deployment files) into `main`, or just point Render at that
branch directly in step 3 - either works.

## 3. Create the Render web service

1. Go to [render.com](https://render.com) and sign up.
2. **New +** -> **Web Service** -> connect this GitHub repo.
3. Render should auto-detect the `Dockerfile` and offer **Docker** as the
   runtime. If it defaults to something else, set the runtime to Docker
   by hand.
4. **Set Root Directory to `copo-attainment`.** This repo's git root is
   one level above the app itself (there's a leftover
   `attainment template.xlsx` sitting at the true repo root next to this
   folder) - without this, Render looks for the Dockerfile at the repo
   root and the build fails with "open Dockerfile: no such file or
   directory". This field is under Settings -> Build & Deploy if you're
   configuring an existing service rather than creating a new one.
5. Pick the **Free** instance type.
6. Under **Environment**, add two variables:
   - `DATABASE_URL` -> the Neon connection string from step 1
   - `SECRET_KEY` -> any long random string (e.g. run
     `python3 -c "import secrets; print(secrets.token_hex(32))"`
     locally and paste the output). This signs login sessions - don't
     skip it and don't reuse the placeholder that's in the source code,
     or anyone who reads the repo could forge a login.
7. Deploy. The first build installs Chromium + Tesseract + all the Python
   deps, so expect it to take several minutes - later deploys are faster
   since layers are cached.

## 4. First run

On first request, the app behaves exactly like a fresh local install: it
creates all tables in the new Postgres database automatically, then
redirects to the one-time coordinator setup screen. Set up the
coordinator account there.

## 5. Things worth knowing about the free tier

- **Cold starts:** a free Render web service spins down after 15 minutes
  with no inbound traffic, and takes about a minute to wake back up on
  the next request. Normal browsing will feel that delay occasionally;
  it's not a bug.
- **The VTU scraper needs the tab to stay open while it runs.** Its
  progress page polls the server every couple of seconds, which counts
  as inbound traffic and keeps the service awake - but if you navigate
  away or close the tab mid-scrape, the service can spin down and the
  in-progress job (which only lives in that process's memory, not the
  database) is lost. Keep the progress page open until it finishes.
- **Single worker, by design:** the Dockerfile runs Gunicorn with
  `--workers 1 --threads 4`. That's not a resource-saving default -
  scrape-job progress (`app/scrape_jobs.py`) is tracked in an in-process
  dict, not the database, so a second worker process would never see
  jobs started on the first one. Don't raise `--workers` without moving
  that state into Postgres first.
- **Neon's 0.5 GB free storage** is generous for this app's data (course
  structures, rosters, marks - all just text/numbers), but if you ever
  outgrow it, upgrading is just changing `DATABASE_URL` to a bigger plan.
