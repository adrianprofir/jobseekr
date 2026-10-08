# Security policy

## Reporting a vulnerability

Please report security problems privately and do not open a public issue.

Use GitHub's private vulnerability reporting: open the **Security** tab of the repository and choose **Report a vulnerability**.
Describe what you found, how to reproduce it and what you think the impact is.

You can expect an acknowledgement within a few days.
This is a volunteer-maintained project, so fixes are made on a best-effort basis; critical issues come first.
Please give us a reasonable time to release a fix before you disclose the problem publicly.

## Supported versions

Only the latest release and the `main` branch receive security fixes.

## Security model

jobseekr is a single-purpose app that is designed to run on a trusted network: your own machine, a home or office LAN, or a private overlay network such as a tailnet or a VPN.

- Every page requires a login, and there is no public sign-up. The administrator creates accounts.
- Uploaded CVs and cover letters are stored outside the static files and are served only through a login-protected view. There is no public media URL.
- The Compose stack publishes the app on `127.0.0.1` by default. The database is never published on a host port.
- HTTPS is opt-in. If you serve the app over HTTPS, set `DJANGO_SECURE_COOKIES` and, behind a reverse proxy, the settings described in the README.
- The app has not been hardened for exposure to the open internet. If you expose it anyway, put it behind a TLS-terminating reverse proxy, keep accounts limited to people you trust, and keep it updated.

## Security-relevant areas

Reports about these areas are especially welcome.

### Link capture and the SSRF guard

Link capture makes the server fetch a URL that a user pasted.
`tracker/capture/fetch.py` is what stops that from becoming a way to reach internal services.
It allows only `http` and `https`, requires every address a host resolves to be public, pins the connection to the address it checked, re-checks every redirect, and caps time, size and content type.
A way around it is a vulnerability, for example:

- reaching a loopback, private, link-local, carrier-grade NAT or cloud-metadata address, directly or through a redirect or DNS trick;
- reading a response body that is not HTML, or exceeding the time and size limits.

### Document uploads and downloads

Uploads are restricted to PDF and DOCX files and a maximum size, and are served only to logged-in users.
Path traversal, serving a file without a login, or stored content that runs in a visitor's browser are all in scope.

### Authentication and sessions

Login, session and CSRF handling, and the account administration in `/admin/`.

## Out of scope

- Problems that need an attacker who already has an administrator account or access to the server.
- Missing hardening on an instance you exposed to the internet without a reverse proxy.
- Findings in third-party dependencies that are already fixed in a newer release (Dependabot proposes those updates).
