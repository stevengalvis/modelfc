# Shared lint gates

Python 3.12 and Node 22 are the CI baseline. From the repository root:

```bash
python3 -m venv .venv-lint
.venv-lint/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-lint.lock
.venv-lint/bin/python -m ruff check .
cd web
npm ci
npm run lint
```

CI runs the same check-only commands before the existing tests, typecheck and
build. No autofix, formatter, network service or type-aware lint project is
involved in the lint run. Dependency installation needs the package registries.
Ruff is isolated from the deployment environment: its exact-distribution check
must still use only the deployment lock. Ruff's version is enforced in config;
its dependency-free wheels are hash locked. Frontend lint dependencies are exact
versions and transitive resolutions/integrities are in `web/package-lock.json`.
Update pins and locks together and review new diagnostics before upgrading.

## Scope and initial rules

Ruff checks authored Python in `src`, `tests`, `ops` (including CI, operational
and factory helpers), `benchmarks`, and `web/scripts`. Its standard exclusions
and Git ignores remain active. Generated, vendor and evidence directories are
not source roots. Add future Python source roots explicitly in `ruff.toml`.
The initial selection is E9, F63, F7 and F82: parsing, invalid comparisons,
control-flow mistakes, duplicate arguments and undefined names. This deliberately
does not adopt formatting, import ordering or unused-import/local cleanup.

ESLint checks JavaScript/TypeScript, JSX/TSX and configuration/test files under
`web`. Dependencies, Next output, build/coverage output and generated
`next-env.d.ts` are excluded. The explicit rules in `web/eslint.config.mjs` catch
unreachable/unsafe control flow, duplicate branches/arguments, invalid comparisons,
Promise executor mistakes, Hooks call-order violations and invalid Next component
or document usage. Parsing is syntax-only; `npm run typecheck` remains independent.
Warnings fail through `--max-warnings=0`; unused suppression directives fail too.
Next 16 uses the ESLint CLI: `next lint` is not used and `next build` is not a lint gate.

## Transparent rollout boundary

Baseline inspection on main `ad4068fd283b4b126bb29139012dc13fcb920337`
found 53 Pyflakes findings beyond one Ruff parsing diagnostic (46 auto-fixable
unused imports, six unsafe-fix candidates and one other finding). Those rules
are deferred rather than spread cleanup across application and operational code.
A semicolon before a variable named `match` parsed in CPython but confused Ruff;
splitting that statement onto two lines is the only Python source adjustment.

The full Next core-web-vitals preset reported nine findings: seven
`set-state-in-effect`, one `immutability`, and one `exhaustive-deps`. Changing
loading/reset state or effect dependencies deserves focused behavioral tests;
it is not bundled into lint setup. Adopt these in a separate reviewed change
covering async loading, cancellation and model selection. No baseline file or
inline suppression hides these findings. The initial rule list is intentionally
explicit; it does not claim full Next/React recommended-preset coverage.
Formatting, unused-symbol cleanup and type-aware linting remain separate work.

## Agent integration contract

Run both commands above on the exact candidate head and retain exit codes and
SHA with the test evidence. A nonzero exit blocks the agent's proposed handoff;
inspect findings rather than adding blanket disables or using autofix unchecked.
Run existing Python tests/dependency checks and frontend typecheck/tests/build
as documented in CI. These commands are available to factory agents; this change
does not wire or attest an installed factory controller.

The lint steps fail the existing `test` and `frontend` PR jobs. Making those jobs
required for merge is a separate GitHub branch-protection/ruleset setting and
requires maintainer approval; workflow configuration alone does not enforce it.
Existing exact-head OFFLINE and release/deployment acceptance contracts remain
unchanged. Draft PRs do not automatically receive Trusted OFFLINE validation.

## Primary references

- [Ruff configuration and discovery](https://docs.astral.sh/ruff/configuration/)
- [Ruff rules](https://docs.astral.sh/ruff/rules/)
- [ESLint flat configuration](https://eslint.org/docs/latest/use/configure/configuration-files)
- [Next ESLint plugin and direct plugin setup](https://nextjs.org/docs/app/api-reference/config/eslint)
- [typescript-eslint parser](https://typescript-eslint.io/packages/parser/)
