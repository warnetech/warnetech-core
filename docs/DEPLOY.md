# Running warnetech-core elsewhere

Start with `warnetech doctor`. It reports Python and platform, container, dependencies,
enabled providers, proxy and CA settings, writable data paths, free memory and GPU,
and with `--network` whether the configured Ollama hosts answer. It never prints a key
or a proxy password, and never contacts hosted providers. `--json` for scripts.

## Where it runs

| Environment | Status | Evidence |
|---|---|---|
| Linux x86_64, Python 3.10, 3.11, 3.12 | Supported | CI, blocking |
| macOS, Python 3.12 | Supported | CI, blocking |
| Windows, Python 3.12 | Supported, with limits | CI, blocking. Interpreter limits (memory, CPU, file size) and `bash` are POSIX-only; `bash` is refused on Windows |
| Linux arm64, Raspberry Pi | Supported | CI on `ubuntu-24.04-arm`; multi-arch image built (amd64+arm64) by `image.yml`. Not run on a Pi |
| Android 64-bit (arm64-v8a), Termux | Verified on one device | `scripts/termux_install.sh` ran to completion natively (Python 3.14, aarch64; pydantic-core compiled from source) and `warnetech doctor` detected Termux. Debian under proot-distro also ran the full suite (742 passed). The test suite was not run in native Termux. 32-bit Android is unsupported. Ollama is not packaged for Termux: point `OLLAMA_HOST` at another machine |
| Kubernetes | Manifests, not applied | `deploy/kubernetes/` (kustomize); checked structurally by tests only, never against a cluster |
| Python older than 3.10 | Not supported | |
| Local weights (`hf` extra) | Python 3.11+ only | `transformers` 5 and `torch` require it |

## Install

| How | Command |
|---|---|
| pip | `pip install -e ".[service]"` |
| Air-gapped | Connected: `pip wheel --wheel-dir wheelhouse ".[service]"`. Offline: `pip install --no-index --find-links wheelhouse "warnetech-core[service]"`. Run end to end here: 24 wheels |
| Container (service) | `docker buildx build --platform linux/amd64,linux/arm64 -f Dockerfile.service -t warnetech-service .` |
| Compose | `WARNETECH_API_TOKEN=<token> docker compose --profile service up --build` |
| Dev container | Open the folder in VS Code or Codespaces (`.devcontainer/`) |
| systemd | `warnetech-monitor.service` |

The service image runs as an unprivileged user, keeps its data in `/data`, has a
health check on `/health`, and takes the API token and provider keys from the
environment at run time. It does not bake in credentials, weights or tokens.

## Network conditions

- **No internet:** `WARNETECH_OFFLINE=1` uses the echo provider. `WARNETECH_LOCAL_ONLY=1`
  refuses every hosted provider and keeps local ones (Ollama, LM Studio, local weights).
- **Proxies and private CAs:** providers use `httpx`, which reads `HTTPS_PROXY`, `NO_PROXY`
  and `SSL_CERT_FILE` by default. Taken from reading the code; not run through a proxy.
  Never disable certificate checks: fix the CA instead.
- **Slow or flaky links:** retries with backoff, per-provider rate limits and fallback
  chains between models. Timeouts are 60 s for hosted providers and 300 s for local ones.
- **Several machines:** `OLLAMA_NODES=gpu=http://10.0.0.5:11434,...`, checked with
  `warnetech ollama nodes`.

## Reaching it remotely

The HTTP service (`Dockerfile.service`) is the remote interface: bearer-token auth, and
it refuses to start without a token. It has **no TLS of its own**. Compose binds it to
`127.0.0.1`; to expose it, put a reverse proxy that terminates TLS in front, and keep the
token. The MCP server is stdio only, so it is local to the machine that starts it.

## Not verified here

- The container images have not been built: the sandbox had no Docker daemon. Their steps
  were run by hand (non-editable install, service start, `/health` 200, protected route
  401 without a token and 200 with one, a completion), and the compose files parse.
- The dev container has not been opened.
- Windows and macOS results come from CI, not from a machine I could run.

## Reaching a device behind NAT

No relay is built here. Use Tailscale or a Cloudflare Tunnel to reach the service, and keep `WARNETECH_API_TOKEN` set.

## Not built yet

Remote MCP over HTTPS (`POST /mcp`), the signed webhook connector, and Slack/Discord adapters.
