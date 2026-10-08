# Running a public demo

This page explains how to host jobseekr as a public demo behind a reverse proxy or a tunnel, where every visitor gets a private sandbox on fictional data.
The kit lives in `deploy/demo/`.
It is separate from the main `compose.yaml`, which is meant for your own applications on a network you trust.
What the demo mode does for a visitor is described in [Public demo mode](../README.md#public-demo-mode).

## What the demo is

- With `DEMO_MODE=true`, a visitor lands on `/try/`, presses "Try the demo" and gets a private sandbox account filled with sample data.
  It is deleted after 24 hours, or at once when the visitor leaves.
- The sample data is fictional: companies, people and documents are made up, with contacts on reserved `.example` addresses and every date relative to the day the sandbox is made.
- Link capture runs with `CAPTURE_MODE=samples`: it only reads the bundled sample postings and never fetches a URL, so a visitor cannot make the server request anything.
- Uploads are off (`DOCUMENT_UPLOADS_ENABLED=false`), and the app refuses to start in demo mode with `EMAIL_HOST` set, so the demo cannot send mail.
- Sandboxes are limited in number and in how fast one visitor address can start them.
  The address comes from `CLIENT_IP_HEADER`, which is `CF-Connecting-IP` behind Cloudflare.
  Only use a header that your proxy always sets, because a client can send any header the proxy does not overwrite.
- `/admin/` still works for an operator account, so keep it private, for example behind your access proxy, or leave it unrouted.

## The stack

`deploy/demo/compose.yml` runs the Compose project `showcase-jobseekr` with three services:

| Service | Role | Networks |
| --- | --- | --- |
| `jobseekr-web` | gunicorn. Migrates on start. | `backend` and the external edge network |
| `scheduler` | Deletes expired sandboxes and sessions every `SCHEDULER_INTERVAL_SECONDS`. | `backend` |
| `db` | Postgres, in a named volume. | `backend` |

- `backend` is an internal network, so `db` and `scheduler` have no route to the internet.
- Only `jobseekr-web` joins the external edge network, under the alias in `DEMO_EDGE_ALIAS`.
- `jobseekr-web` also publishes `127.0.0.1:${DEMO_SMOKE_PORT}` for smoke tests from the host.
  It never binds a LAN or public address.
- The image is `showcase-jobseekr-app:latest` and the project is `showcase-jobseekr`.
  The main stack builds `jobseekr:latest` under the project `jobseekr`, so building the demo on the same machine never retags the image, or touches the volumes, of your own instance.
- Every service restarts unless stopped.
  `jobseekr-web` has a `/healthz` healthcheck that includes the database, `db` uses `pg_isready`, and `scheduler` writes a heartbeat after every pass.
- Uploads are off, but each sandbox's sample PDFs are stored files, so `jobseekr-web` and `scheduler` share the named volume `documents`, and deleting a sandbox deletes its files.

## Naming on a shared edge network

A container on several Docker networks resolves names on all of them, and Compose registers a service's own name as an alias on every network the service joins.
If several stacks share an edge network, a service called `web` on it would make this demo answer to `web` too, and requests meant for another stack's `web` would sometimes land here.
So:

- the service that joins the edge network is called `jobseekr-web`, never `web`;
- the proxy or tunnel reaches the demo by `DEMO_EDGE_ALIAS`, never by a bare name such as `web` or `db`;
- CI starts the stack next to a decoy container answering to `web` on the edge network and checks that the alias reaches jobseekr, `web` does not, and `db` and `scheduler` are not on the edge network at all (`deploy/demo/smoke-test.sh`).

## Setup

You need Docker with the Compose plugin and a reverse proxy or tunnel that can reach a Docker network.

1. Create the edge network once.
   Use the name you will put in `DEMO_EDGE_NETWORK`:

   ```sh
   docker network create showcase-jobseekr-edge
   ```

2. Configure the stack:

   ```sh
   cd deploy/demo
   cp .env.demo.example .env
   chmod 600 .env
   ```

   Set `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`, and set `DEMO_HOSTNAME` to the public hostname without a scheme.
   `.env.demo.example` explains the other variables.
   It holds no mail setting, and it must stay that way.

3. Start it:

   ```sh
   docker compose up -d --build --wait
   ```

4. Smoke test from the host.
   The loopback port serves the demo when the request carries the public host and the forwarded protocol, as your proxy would send them:

   ```sh
   curl -L -H "Host: $DEMO_HOSTNAME" -H "X-Forwarded-Proto: https" http://127.0.0.1:8095/try/
   ```

   `./smoke-test.sh` runs the full set of checks: it starts and leaves a sandbox, tests the alias on the edge network, and checks the network isolation.

## Putting it behind a proxy or tunnel

Point the public hostname at `http://<DEMO_EDGE_ALIAS>:8000`, from a proxy or tunnel container that is on the same edge network.
For example, with a Cloudflare Tunnel running in a container on that network, set the public hostname's service to `http://showcase-jobseekr-web:8000`.
With a reverse proxy such as Caddy or nginx on the same network, use the same upstream.

The stack expects TLS to end at the proxy:

- the proxy forwards plain HTTP and sets `X-Forwarded-Proto: https`;
- `TRUST_X_FORWARDED_PROTO` is on, because only the proxy can reach the container;
- secure cookies, the HTTPS redirect (`/healthz` is exempt), and HSTS are on.
  Lower `SECURE_HSTS_SECONDS` while you are still testing, because browsers remember it.

If the proxy runs on the host instead of in Docker, use the loopback port as the upstream: `http://127.0.0.1:8095`.
Also put a rate limit at the edge in front of the Try page, as the in-app limit is a second line of defence.

## Operating it

- Restarting keeps the sandboxes that have not expired.
- Delete every sandbox by hand with `docker compose exec scheduler python manage.py expire_sandboxes --all`.
- To update, pull the new code and run `docker compose up -d --build --wait` again.
- Logs: `docker compose logs -f`.
  `scheduler` logs every pass, and `jobseekr-web` logs creating, refusing, leaving and expiring a sandbox with the visitor's address.
  The Try page tells visitors how long those logs are kept (`DEMO_LOG_RETENTION_DAYS`), so make that match your log retention.
- Demo data is disposable, but the volumes `showcase-jobseekr_pgdata` and `showcase-jobseekr_documents` are not removed by `docker compose down`.
  Add `-v` to delete it.
