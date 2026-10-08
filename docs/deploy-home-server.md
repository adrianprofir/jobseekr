# Worked example: a home server

This page describes one complete way to run jobseekr at home.
It is an example to adapt, not the only supported setup.
The addresses and paths below are placeholders: `192.0.2.10` is a documentation address standing in for your server's LAN address, and `homeserver.example` for its name.

## The shape of the setup

- One small Linux machine on your home network runs Docker and Docker Compose.
- jobseekr is published on the machine's LAN address only, so it is reachable from your home network and nowhere else.
- Optionally, the same app is also reachable over a Tailscale tailnet, through a small relay container.
- A nightly cron job takes a backup, and a weekly cron job sends the summary e-mail.
- Nothing is exposed to the internet: no port forwarding, no public reverse proxy.

## 1. Install

```sh
sudo mkdir -p /opt/jobseekr && sudo chown "$USER" /opt/jobseekr
git clone https://github.com/adrianprofir/jobseekr.git /opt/jobseekr
cd /opt/jobseekr
cp .env.example .env
chmod 600 .env
```

Edit `.env`:

```sh
DJANGO_SECRET_KEY=<a long random value>
POSTGRES_PASSWORD=<a strong password>
BIND_ADDRESS=192.0.2.10
DJANGO_ALLOWED_HOSTS=192.0.2.10,homeserver.example
DJANGO_CSRF_TRUSTED_ORIGINS=http://192.0.2.10:8000,http://homeserver.example:8000
TIME_ZONE=Europe/Copenhagen
```

Start it and create your login:

```sh
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Open `http://192.0.2.10:8000` from a device on your network.
Reserve the server's LAN address in your router (a DHCP reservation) so it does not change.
If it does change, Docker can no longer publish on it and the web container will not start until `BIND_ADDRESS` is updated.

## 2. Also reachable over Tailscale

`BIND_ADDRESS` takes a single address.
To also reach the app from your tailnet, run a small relay container that listens on the server's Tailscale address and forwards to the LAN listener.
Copy the example override next to `compose.yaml` (Compose loads `compose.override.yaml` automatically and it is git-ignored):

```sh
cp deploy/compose.override.example.yaml compose.override.yaml
```

Then add the server's Tailscale address (shown by `tailscale ip -4`) to `.env`:

```sh
TAILSCALE_ADDRESS=100.x.y.z
```

Add the Tailscale address, the MagicDNS name and their `http://...:<WEB_PORT>` origins to `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`, then run `docker compose up -d`.
Never publish on `0.0.0.0`.

Why a relay instead of a second port binding on `web`: Docker cannot publish on an address that does not exist yet, and at boot Docker starts before `tailscaled` has assigned the Tailscale address.
A failed bind on `web` takes the whole container down, including its LAN listener.
The relay is a separate `socat` container on the host network.
It exits until the Tailscale address exists and its restart policy retries it, so the LAN listener never depends on Tailscale and the Tailscale listener appears by itself once the address is up.

Link capture refuses addresses in the carrier-grade NAT range, which includes Tailscale, so a pasted link can never make the server reach into your tailnet.

## 3. Scheduled jobs

Use the crontab of a user that can run Docker (`crontab -e`).

```cron
# Nightly backup at 03:30, kept off the server's own disk.
30 3 * * * cd /opt/jobseekr && scripts/backup.sh /srv/backups/jobseekr >> /var/log/jobseekr-backup.log 2>&1

# Weekly summary e-mail on Monday at 08:00 (needs EMAIL_HOST in .env and an e-mail address on your account).
0 8 * * 1 cd /opt/jobseekr && docker compose exec -T web python manage.py send_weekly_summary >> /var/log/jobseekr-summary.log 2>&1
```

Both commands log what they do and exit with an error on failure.
Point your log monitoring or a cron mail alert at those log files so a failed backup does not go unnoticed.
Copy the backup directory to another machine as well: the backups contain your CVs and personal data.

## 4. Updating

```sh
cd /opt/jobseekr
scripts/backup.sh /srv/backups/jobseekr
git pull
docker compose up -d --build
docker compose ps
```

Migrations run when the web container starts.

## 5. A stack that was created as `job-tracker`

Before the rename to jobseekr this project was called job-tracker, and its Compose project was named `job-tracker`.
If your stack was created under that name, Compose would create new, empty volumes for the renamed project and the app would look empty.
Pin the old name before you deploy renamed code:

```sh
echo 'COMPOSE_PROJECT_NAME=job-tracker' >> .env
docker compose config | grep '^name:'      # must print: name: job-tracker
docker volume ls | grep job-tracker        # job-tracker_postgres-data, job-tracker_documents
```

Only then run `docker compose up -d --build`.
The image name changes to `jobseekr:latest`; that does not affect your data.
