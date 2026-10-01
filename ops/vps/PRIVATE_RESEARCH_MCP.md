# Optional private research MCP dogfood

This repository supplies a stdio MCP adapter and **uninstalled** host templates.
It does not activate a service, tunnel, ChatGPT connection or public endpoint.
Internal Intelligence V1 remains the only source of research calculations and
snapshot publication. This private MCP is not a public Zeno plugin contract.

## Supported connection and prerequisites

OpenAI's [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
can launch a local stdio MCP command through an outbound connection; ChatGPT
developer mode selects the private tunnel as a connection. There is no listening
MCP socket, inbound firewall rule, DNS record, Caddy route or OAuth customer
flow. Initialize/discovery/calls use the pinned MCP Python SDK v2 from
`requirements-mcp.lock`. The base production `requirements-deploy.lock` remains
unchanged; install the optional graph in a *separate* interpreter, not a release
`.venv`.

Current [ChatGPT developer-mode guidance](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
says full MCP write actions are available in Business/Enterprise/Edu beta;
Pro developer mode is read/fetch only. `create_research_snapshot` is correctly
annotated as a write; do not misrepresent it as read-only to enable it on Pro.
If a personal account cannot use the write tool, create one snapshot with the
existing authorized private operator CLI and use the three read tools with its
ID. Confirm workspace entitlements before setup.

## Four tools and snapshot reuse

| Tool | Arguments | Effect |
| --- | --- | --- |
| `create_research_snapshot` | none | Appends one private manifest and returns ID, cutoff, E1/team-corners coverage, warnings and limitations. Write, non-idempotent, non-destructive. |
| `research_summary` | `snapshot_id` (32 lower-hex) | Returns existing deterministic summary. Read-only. |
| `segment_comparison` | `snapshot_id`, `segment` (`venue` or `venue_history`) | Returns existing fixed groups. Read-only. |
| `inspect_fixture` | `snapshot_id`, `prediction_id` (32 lower-hex) | Returns existing bounded member fixture. Read-only. |

Create once at the beginning of an investigation and reuse its ID for every
follow-up. Create another only when refreshed/current evidence is explicitly
requested. Read tools never create snapshots. Missing shadow evidence is not a
model loss; decision snapshots are not placed bets; hypothetical units/ROI are
not real betting results. No arbitrary paths, SQL, code, cohorts or provider
access are tool arguments. V1 bounds normal JSON to 64 KiB; the adapter also
checks the serialized MCP output and fails with `RESEARCH_OUTPUT_LIMIT`.

Errors are stable fixed codes: `INVALID_RESEARCH_ID`,
`UNSUPPORTED_RESEARCH_SEGMENT`, `PREDICTION_NOT_IN_SNAPSHOT`,
`RESEARCH_LIMIT_EXCEEDED`, `RESEARCH_OUTPUT_LIMIT`,
`INVALID_RESEARCH_ARGUMENTS`, `UNSUPPORTED_RESEARCH_TOOL` and validated V1
storage/evidence codes. Other failures map to `RESEARCH_EVIDENCE_UNAVAILABLE`.
No traceback, raw exception or filesystem path is returned. A failed create may
have published the manifest before a later summary read failed; inspect private
operator evidence before retrying. No provider work is retried.

## Host trust boundary and separately authorized activation

The optional unit runs as the existing trusted `modelfc-runtime` writer,
**not** `modelfc-api`. A new Unix identity would need ACLs on every existing
and future private evidence family and lock, plus ownership of the snapshot
directory required by V1. That is a larger publication change. The unit
confines the trusted runtime process with `ProtectSystem=strict`: only private
`research-snapshots` and tunnel profile storage are writable. No OddsPapi
credential is loaded; its directory is inaccessible. The tunnel key is passed
only to the tunnel client. `research_mcp_launch.py` replaces the MCP child's
environment, stripping that key and inherited secrets. The runtime UID remains
a trusted writer; the filesystem sandbox is defense in depth, not a claim of
a separate Unix security identity. The MCP module has no shell, path selector,
provider client or public API route.

Before separately authorizing installation, review the current `tunnel-client`
binary and its permissions, the ChatGPT workspace and Tunnels Read+Use/Manage
entitlements. Install root-owned non-writable copies of
`research_tunnel_launch.py` and `research_mcp_launch.py` under
`/opt/modelfc-ops`. Create a root-owned optional CPython 3.12 environment at
`/opt/modelfc-research/.venv`; install `requirements-deploy.lock` then
`requirements-mcp.lock` with `--require-hashes --only-binary=:all:`, and run
`pip check`. Pin and inspect the separately downloaded tunnel client under
`/opt/modelfc-research/bin`. Neither key nor profile belongs in Git. Supply
`/etc/modelfc/research/tunnel.key` only with the systemd `LoadCredential`
mechanism; rotate it separately from OddsPapi and deployment credentials.

The unit assumes runtime-owned mode-0700 directories `/var/lib/modelfc-research`
(profile) and `/var/lib/modelfc/state/research-snapshots` (empty is valid), plus
the real `state/prospective/runner.lock`. Creating the empty private snapshot
directory during authorized installation creates no manifest or fake state
lock. Verify sandbox read access to immutable evidence while control/budget
remain read-only. On each start, systemd resolves the physical directory at
`WorkingDirectory=/srv/modelfc/current`; the MCP launcher validates that
physical release and protected deployed SHA before importing release source.
An older release without the adapter fails closed; stop the optional tunnel
before rolling back to it. The optional venv and root-installed launchers need
separately reviewed updates when contracts change.

Following OpenAI's tunnel CLI guidance, initialize a named local stdio profile
with the selected tunnel ID and MCP command:

```sh
tunnel-client init --sample sample_mcp_stdio_local \
  --profile zeno-private-research --tunnel-id <private-tunnel-id> \
  --mcp-command '/usr/bin/python3 -I -B /opt/modelfc-ops/research_mcp_launch.py'
tunnel-client doctor --profile zeno-private-research --explain
```

Initialize as the runtime identity with the tunnel key available only for that
setup command; never paste it into shell history, a unit or tracked config.
Confirm CLI/profile layout with installed `tunnel-client help quickstart`.
Statically verify and separately authorize enabling/starting the unit.
`research_tunnel_launch.py` reads the systemd credential and runs
`tunnel-client run --profile zeno-private-research`; its MCP child has no token
environment. Verify `doctor`, inspect exactly four tools through a personal
ChatGPT Plugins developer-mode draft using Tunnel, and test approved evidence.
Do not publish the draft or grant others access.

To disconnect, remove the personal draft connection, stop/disable the unit,
revoke the tunnel key and retire the private tunnel/profile as appropriate.
No public ingress or production collection service needs reloading. Existing
immutable snapshots remain. Older runners/status readers ignore them. Keep
referenced evidence during retention/rollback. Avoid creation near hourly
:05 UTC: it holds the shared runner lock, so an overlapping runner may report
BUSY.

Private results include bookmakers, odds, no-vig, edges, champion/shadow
decisions and hypothetical W/L/P, units and ROI. A future public plugin needs
a separately reviewed football-intelligence projection and current OpenAI
policy review; never expose this private schema as a public API by default.
