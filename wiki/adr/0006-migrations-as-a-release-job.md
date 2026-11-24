# ADR-0006: Migrations run in a Job that is a plain release resource

## Status

Accepted. This replaces an earlier plan for a Helm hook (see Alternatives).

## Date

2026-11-22

## Context

The original backend migrated the database at startup. With several replicas that is a race, and a new Pod could
serve before the schema it needs exists. The schema has to lead the code, and a failed migration has to fail the
release rather than leave a half-rolled deployment.

## Decision

The app never migrates. The chart renders the migration Job as an ordinary release resource, not a Helm hook, and the
deploy runs `helm upgrade --install --rollback-on-failure --wait --wait-for-jobs`, so Helm waits for the Job and a
failed Job fails (and rolls back) the release. The backend checks the schema revision read-only and is not ready
until the database is at head, so no new Pod serves on an old schema. The Job takes its database settings from an
explicit env list, from values or `existingSecret`, never from the chart's ConfigMap. Migrations follow
expand/contract, and squawk lints their SQL in CI.

## Consequences

- Good: exactly one migrator per release; the schema leads the code through readiness; a failure is visible as a
  failed release with the Job's logs printed.
- Good: the Job is part of the release's manifest (`helm get manifest` lists it), but it carries
  `helm.sh/resource-policy: keep`, so a rollback or an uninstall leaves it in place and its logs survive; its TTL
  removes it, and for a preview the preview cleanup deletes it.
- Bad: during the Job, new Pods start and stay unready until it finishes; the rollout is as slow as the migration.
- Bad: the Job is named per revision (`<fullname>-migrate-r<revision>`, a Job's spec is immutable) and kept for the
  [`migration-job.yaml`](../../charts/your-service-name/templates/migration-job.yaml) `ttlSecondsAfterFinished`; `helm upgrade` is the supported path, and a tool that applies rendered manifests
  meets the previous Job's immutable spec.
- Bad: expand/contract is a discipline: a destructive change needs two releases.

## Alternatives considered

- A `post-install`/`post-upgrade` hook: rejected, it deadlocks with `--wait`, because Helm runs it only after the
  Pods are ready, and they wait for the schema.
- A `pre-install`/`pre-upgrade` hook: rejected, it runs before the chart's Secret and ConfigMap exist unless they
  become hooks too; hook objects are not part of the release, and GitOps tools re-map hooks.
- Migrate at startup with a lock: rejected, a slow migration blocks every replica's start, and code still races the
  schema.

## Links

- [`charts/your-service-name/templates/migration-job.yaml`](../../charts/your-service-name/templates/migration-job.yaml)
- [`scripts/ci/deploy-env.sh`](../../scripts/ci/deploy-env.sh), [`scripts/ci/squawk.sh`](../../scripts/ci/squawk.sh),
  [`scripts/ci/preview-cleanup.sh`](../../scripts/ci/preview-cleanup.sh)
- [Database: the migration Job](../database.md#the-migration-job),
  [expand/contract policy](../database.md#expandcontract-policy),
  [readiness and liveness](../database.md#readiness-and-liveness)
