# Save-once VPS credentials

This helper is prepared offline. It neither deploys itself nor authorizes an API
run. Importing it does not read credentials. The current caller/probe is unchanged;
a future approved caller must explicitly adopt `read_credentials()`.

## Exact storage and approval

`pwd.getpwnam("zeno-worker")` must resolve UID 1003, and both real/effective UID
must be 1003. The helper uses that account's canonical absolute home, never HOME
or a command-line path. Resolve and show the actual destination before staging:

`<verified passwd home>/.config/modelfc/agents-credentials/`

The directory is 0700. `application.key`, `environment.key`, and the non-secret
completion marker `ready` are separate regular 0600 files owned by UID 1003.
Root and processes running as this account can read them. Backups containing
these files can retain them. This is not encryption or protection from a
compromised host. File separation does not isolate processes sharing the UID.

User approval must cover saving both existing keys here and using them only for
API runs the user approves. It does not authorize another run, retry, broader
permissions, rotation, or deletion. The terminal SAVE prompt confirms the local
operation; it is not a substitute for the assistant's required approval.

## One final private terminal entry

After approved staging of the reviewed files into the trusted checkout, run as
the existing worker in a private, unrecorded foreground terminal:

```sh
python -B -m ops.bounded_worker.agents_credentials --save-approved-once
```

Use the previously verified interpreter/dependencies and checkout. No install,
new account, ownership repair, service change, or security-setting change is
part of this operation. All standard streams must be the owned foreground
terminal. Do not invoke through a terminal automation tool that records inputs.
Do not put keys in arguments, environment variables, chat, commands, screenshots,
or files created by other tooling. Type SAVE and enter the two keys at the hidden
prompts. Echo fallback is fatal. Terminal attributes are restored on normal
return or Python interruption; abrupt process/host death can prevent restoration.

The helper follows no symlinks, walks held directory descriptors, rejects
untrusted writable ancestors, and checks owner/modes. Missing .config/modelfc
parents are created privately; existing safe ancestors are not modified. An
existing credential directory always blocks saving, even if incomplete. Files
are created exclusively. It does not overwrite, chmod, chown or adopt contents.

Both key files are synced and read back for exact equality before creating the
completion marker. The marker is synced and read back before confirming success. Failure can
leave a private incomplete directory. The reader refuses a missing/invalid
marker. Do not rerun to repair it or assume failure means nothing was written;
inspect metadata only and obtain separate approval for replacement/removal.
A crash after the final marker may leave a complete store despite missing success
output. No recovery path prints existing key values.

## Approved automated reading

`read_credentials()` returns `(application_key, environment_key)` in memory.
`read_application_key()` uses the same store/marker/selected-file checks but
never opens or loads the environment key, for the idle probe. Neither reader
needs a TTY or makes network requests. It resolves the fixed identity and
validates directories, regular file type, owner, exact mode, single hard link,
bounded size, syntax, distinct values and completion marker. It fails rather
than silently fixing permissions. Never print/repr the returned tuple or include
it in exceptions, results or debug logs. Python does not guarantee zeroization.

Keep the application key host-only. Keep both credential paths outside all
executor mounts, images and project bundles. Pass only the environment key
through the already reviewed registration stdin route. Do not export either key
as process environment or argv. Retain durable admission/no-retry rules when
wiring the reader into a separately approved caller; possession is not approval.

Confirm whether existing backups cover the directory before saving. Use an
already authorized exclusion mechanism if available; do not silently modify
backup policy. If included, disclose backup access/retention. Later rotation
needs explicit replacement approval and separate provider revocation; changing
local files neither revokes old keys nor erases backups.

## Offline verification

Tests use only synthetic values and private temporary files in the workspace.
They do not touch the actual worker account/home or use a real terminal/API.

```sh
python -B -m unittest -q tests.test_agents_credentials
```
