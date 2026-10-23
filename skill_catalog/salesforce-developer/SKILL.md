---
name: salesforce-developer
description: Implements Salesforce DX work in an already-authenticated org — Apex, Lightning Web Components, custom objects/fields, permission sets, and metadata. Use when the ticket or repo is Salesforce, SFDX, force-app, Apex, LWC, or `sf` CLI related.
---

# Salesforce developer

The sandbox org is **already logged in** as the default target (`sf org login sfdx-url` ran at container start). Do not run `sf org login web`. Do not print, commit, or copy SFDX auth URLs, `.sfdx/`, or `~/.sfdx/`.

Prefer `sf` over deprecated `sfdx force:` commands.

## Before changing anything

1. Confirm the project shape: `sfdx-project.json` at the repo root, source usually under `force-app/main/default/`.
2. Confirm the org: `sf org display` (default alias is already set).
3. Retrieve before editing metadata that already exists in the org, so you do not clobber remote changes:

```bash
sf project retrieve start --metadata ApexClass:MyClass
# or
sf project retrieve start --source-dir force-app/main/default/classes/MyClass.cls
```

4. Match existing naming, API versions (`sfdx-project.json` `sourceApiVersion`), and test patterns in this repo. Do not invent a second package directory.

## Implement

- **Apex**: one class = `.cls` + `.cls-meta.xml`. Tests in `*Test.cls` with `@IsTest`. No SOQL/DML in loops; use maps, `WITH USER_MODE` / `WITH SECURITY_ENFORCED` for user-facing queries, and bulk-safe triggers (one trigger per object, logic in a handler).
- **LWC**: `componentName/{componentName.js,html,js-meta.xml}` (and `.css` if needed). Camel-case JS, kebab-case in markup. Do not mix Aura and LWC for new work unless the ticket says so.
- **Custom metadata / objects / fields**: edit source XML under `objects/`, `objectTranslations/`, `flexipages/`, `permissionsets/`. Never hand-edit `*.xml` API versions to a newer number than `sourceApiVersion` unless the ticket requires it.
- **Profiles** are brittle in git; prefer **permission sets** when adding access.

Put detailed CLI flags and metadata type names in [reference.md](reference.md).

## Verify

Deploy to the default org, then run relevant Apex tests (not the entire org unless the ticket says so):

```bash
sf project deploy start --source-dir force-app --wait 10
sf apex run test --tests MyClassTest --result-format human --wait 10 --code-coverage
```

If deploy fails on missing dependencies, retrieve the missing metadata or include it in the same deploy — do not `--ignore-conflicts` / `--ignore-warnings` to hide a real problem.

## Git

Do not commit:

- `.sfdx/`, `.sf/`, `auth.json`, anything containing an SFDX auth URL
- `*.log`, local `coverage/` output

Commit only source under the package directories plus tests. Open the PR against the project's default branch as instructed.

## Do not

- Call production orgs. This sandbox's default org is the target.
- Run `sf org login web` or JWT login unless auth is actually missing (`sf org display` fails).
- Push with `sf project deploy start --dry-run` as the only check when tests are required — dry-run is extra, not a substitute for Apex tests.
- Change `sfdx-project.json` package directories or namespace unless the ticket asks.
