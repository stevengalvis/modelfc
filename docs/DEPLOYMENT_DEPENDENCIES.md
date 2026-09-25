# Reviewed deployment dependencies

Target: Ubuntu 24.04, CPython 3.12, Linux x86_64.

`requirements.txt` retains developer-friendly ranges. `requirements-deploy.lock`
is the reviewed runtime plus offline-test artifact set. It pins 16 distributions
and one verified wheel hash per distribution. Pydantic is explicit because Model
FC imports it directly. HTTPX, HTTPcore and certifi support TestClient; no separate
test lock is needed. Standard-library unittest needs no additional package.

The initial versions match the existing tested environment, not a new upgrade.
All wheels are `py3-none-any` except pydantic-core, whose reviewed wheel is
`cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64`.
No extras, sdists, local/VCS requirements, nested requirements or alternate
indexes are permitted. Do not add fastapi[standard] or uvicorn[standard].

## Fresh acceptance

Use a new Python 3.12 environment with no system site-packages. Do not upgrade pip
incidentally. Python and its venv bootstrap tooling come from the approved host
or CI setup. Only pip and setuptools, if present from bootstrap, are excluded
from application-distribution inventory checks; they are not runtime lock entries.

```sh
python3.12 -m venv /tmp/modelfc-lock-acceptance
/tmp/modelfc-lock-acceptance/bin/python -m pip install --require-hashes --only-binary=:all: --no-cache-dir -r requirements-deploy.lock
/tmp/modelfc-lock-acceptance/bin/python -m pip check
/tmp/modelfc-lock-acceptance/bin/python -B tests/test_deployment_dependencies.py --check-installed
PYTHONPATH=src /tmp/modelfc-lock-acceptance/bin/python -m unittest discover -v
/tmp/modelfc-lock-acceptance/bin/python -m compileall -q src tests
```

Use a previously nonexistent environment path. CI performs this acceptance on
Ubuntu 24.04/Python 3.12. Download/install requires the package index; the Model FC
suite itself uses offline fixtures and mocks. Negative tests use synthetic local
wheel/sdist archives, `--no-index`, and pip dry-run resolution. They never install
fixture packages or contact an index. They prove wrong hashes, missing transitive
hashes and source-only candidates fail, with successful local-wheel controls.

## Manual updates and hash verification

1. Review requested version changes and dependency release notes. Keep changes
   focused; do not resolve developer ranges opportunistically at deployment time.
2. On the target platform, obtain release metadata from
   `https://pypi.org/pypi/<package>/<version>/json`. Select an unyanked compatible
   wheel for each required distribution, including all active transitive edges.
3. Download the wheel URL from that metadata. Calculate SHA-256 over the actual
   bytes with `sha256sum` or `python -m pip hash`. Require equality with the
   published `digests.sha256`; abort on mismatch. Do not copy unverified hashes.
4. Inspect the wheel's `.dist-info/METADATA`: name, version, Requires-Python and
   every Requires-Dist active for CPython 3.12/Linux x86_64 without extras. Ensure
   every dependency is represented and every version constraint is satisfied.
5. Record only that wheel's verified SHA-256 beside an exact `==` pin. Retain the
   wheel filename comment. Never add an sdist hash or unrelated platform hash.
6. Update the reviewed EXPECTED graph in the structural tests, review the lock
   diff, and run fresh acceptance, pip check, inventory, full suite and compilation.
   Record wheel sizes and environment size in the PR. Wheels need not be committed.

The lock enforces `--require-hashes` and `--only-binary=:all:`; installation also
passes both explicitly and disables cache. Do not use `--no-deps`: pip must check
that the complete dependency closure is pinned and hashed. A new unlisted
transitive dependency must fail rather than be silently installed.

## Trust and deployment boundary

Hashes identify reviewed artifacts; they do not prove dependency code is safe.
Dependency artifacts are part of the trusted Python runtime. Wheels avoid source
build execution, but installed Python/native code still executes when imported.
The Python interpreter, OS libraries and bootstrap pip are outside this lock.

This PR does not modify or enable VPS deployment, change the trusted PR validator,
or resolve disk bounding. PR #58 remains separate. A future reviewed controller
change must consume this lock with the enforcement flags, fail if it is missing,
and never fall back to requirements.txt. Deployment approval remains separate.
