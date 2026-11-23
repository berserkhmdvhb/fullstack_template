# Disaster Recovery

How the service's data is recovered in each environment, how fast, and how the recovery is rehearsed. The
backup jobs themselves are in [Database, Database Backup CronJob](./database.md#database-backup-cronjob);
day-to-day backup checks and the alerts are in the [Operations Runbook](./operations-runbook.md#backup--recovery).
Names in angle brackets are placeholders: `<release>` is the Helm release and `<fullname>` its full name,
`<name>` the chart's name (the dumps' prefix), `<app-ns>` and `<db-ns>` its namespaces, `<instance>` the Cloud SQL
instance, `<app>` a short name for the Backup for GKE plans, `<target-cluster>` the cluster a restore goes to and
`<backup>` one backup's name, `<active-job>` a running Job. The `gcloud` commands read `PROJECT_ID`, `REGION` and `CLUSTER`
from [GKE cluster setup, Set your variables once](./gke-cluster-setup.md#3-set-your-variables-once). Cloud CLIs
change: check each command's `--help` before you rely on it.

## Contents

1. [Targets](#targets)
2. [Production: Cloud SQL point-in-time recovery](#production-cloud-sql-point-in-time-recovery)
3. [Namespace recovery with Backup for GKE](#namespace-recovery-with-backup-for-gke)
4. [The in-cluster database](#the-in-cluster-database)
5. [Drills](#drills)
6. [Ownership](#ownership)

## Targets

The template's defaults. An adopter sets its own and changes the settings they rest on to match.

| Environment | RPO (data you may lose) | RTO (time to serve again) |
|---|---|---|
| Production, Cloud SQL with point-in-time recovery | the writes from the chosen point to the stop of writes: up to about 1 hour after the decision | about 1 hour |
| The in-cluster database (dev, kind) | 24 hours | about 30 minutes |

- **Cloud SQL, RPO: from the chosen point to the stop of writes.** Point-in-time recovery replays the transaction
  log (shipped continuously, not nightly) to any second within its retention (the log retention days below), so the
  clone can start at any point. What it lacks is every write from that point to the moment writes to the live
  instance stop. The [Recovery procedure](#recovery-procedure) stops them in step 5, after the clone, its check and
  the production deploy path, so up to about an hour after the decision; those writes stay in the old instance, to
  reconcile by hand. Stopping the instance at the decision instead (step 5's command, first) cuts that loss to the
  time from the chosen point to the decision, and keeps the service down for the whole hour.
- **Cloud SQL, RTO about 1 hour.** A clone of the instance (its time grows with the database's size; minutes for a
  small one), checking it, then the production deploy path: a pull request with the new database values, a `v*` tag,
  the release build and an approval ([Deployment with CI/CD](./operations-runbook.md#deployment-with-cicd)), then the
  migration Job and the deploy's checks. The hour assumes a reviewer and an approver on hand and a release build of
  about 20 minutes. Measure yours in a [drill](#drills).
- **In-cluster, RPO 24 hours.** One dump a night (`postgresql.backup.schedule`); everything written since the newest
  dump is lost. `BackupTooOld` fires when that dump is older than its threshold
  ([runbook](./operations-runbook.md#backuptooold)).
- **In-cluster, RTO about 30 minutes.** Stop the backend, set the current database aside, restore the dump into a
  new one, redeploy. It
  grows with the dump's size. The dumps live on a volume beside the database (on a node-local class, its disk): a
  lost node or disk can take both, so copy dumps off the cluster if this environment's data matters.

## Production: Cloud SQL point-in-time recovery

Production uses a managed database (`postgresql.enabled: false`), so the chart runs no backup there: the instance's
own backups are the recovery path.

### What must be on

- **Automated backups**, with a retention (the number of daily backups kept) that covers how far back you may need to go.
- **Point-in-time recovery**, which keeps the transaction log.
- **Transaction log retention days**: how far back a point can be (at most 7 days on the Enterprise edition, 35 on
  Enterprise Plus, and never beyond the oldest retained backup).

```bash
gcloud sql instances patch <instance> --project "$PROJECT_ID" \
  --backup-start-time=02:00 --retained-backups-count=14 \
  --enable-point-in-time-recovery --retained-transaction-log-days=7
gcloud sql instances describe <instance> --project "$PROJECT_ID" --format='yaml(settings.backupConfiguration)'
```

A regional (high availability) instance fails over to its standby on a zone outage by itself; that is not a
recovery. Point-in-time recovery is for data that is wrong or gone: a bad migration, a bad write, a deleted table.

### Finding the timestamp

Pick the last moment the data was good, in UTC (RFC 3339), just before the first bad write:

- The backend's structured logs, by request id, for the request that did the damage
  ([Log Correlation & Tracing](./operations-runbook.md#log-correlation--tracing)).
- The release history, for a bad migration: the time of the deploy whose migration Job ran it
  (`helm history <release> -n <app-ns>`).
- The instance's admin operations: `gcloud sql operations list --instance <instance> --project "$PROJECT_ID"`.
- The latest point you can recover to: `gcloud sql instances get-latest-recovery-time <instance> --project "$PROJECT_ID"`.

### Recovery procedure

The template has no maintenance mode: writes stop when the backend loses its database (step 5).

1. **Clone the instance to the timestamp.** The clone is a new instance; the live one is untouched.

   ```bash
   gcloud sql instances clone <instance> <instance>-pitr-<yyyymmdd> --project "$PROJECT_ID" \
     --point-in-time='<yyyy-mm-ddThh:mm:ssZ>'
   ```

   The clone gets its own connection name and address (on a private IP, an address in the same allocated range).
2. **Verify the clone before any traffic reaches it.** Connect to it (Cloud SQL Studio, or a local Cloud SQL Auth
   Proxy) and check that `alembic_version` holds the revision you expect, that the rows the incident touched are as
   they were, and that the database users (and the IAM database users, with IAM authentication) exist.
3. **Choose the release that will run on the clone.** Normally the current one: its migration Job brings the clone
   from the timestamp's schema revision to the release's. **After a bad migration, not that release**: its Job would
   run the bad migration again on the clone. Ship a release that reverts the migration (and the code that needs it),
   or the last good release's code; under the [expand/contract policy](./database.md#expandcontract-policy) the
   previous release's code runs on the older schema.
4. **Prepare the cutover.** In `values.production.yaml`, change the values that name the database
   ([Database, TLS to the database](./database.md#tls-to-the-database)): `cloudSqlProxy.instanceConnectionName`
   with the Auth Proxy, or `externalDatabase.host` (and `externalDatabase.egressCIDR` if the range differs) for a
   direct or Private Service Connect connection; a new server CA in `database.tls.caSecret` for the direct paths.
   Credentials in Secret Manager stay valid when the users came over with the data. Take it through the production
   path ([Deployment with CI/CD](./operations-runbook.md#deployment-with-cicd)) up to the approval of the production
   deploy, and hold it there.
5. **Stop writes to the live instance, then approve the deploy.** Stopping the instance keeps its data:

   ```bash
   gcloud sql instances patch <instance> --project "$PROJECT_ID" --activation-policy=NEVER
   ```

   The old backend Pods lose their database and turn unready, so no request reaches them: their readiness probe
   checks the database ([API reference, `GET /api/v1/health`](./api-reference.md#get-apiv1health), answering
   `database_unreachable`). The deploy's new Pods start on the clone, stay unready (`schema_behind`) until the
   migration Job finishes, and the deploy's checks then prove the service
   ([Database, Readiness and liveness](./database.md#readiness-and-liveness)). The service is down
   from the stop to the first ready Pod. Writes between the chosen point and the stop are only in the old instance:
   if they matter, start it again (`--activation-policy=ALWAYS`) and reconcile them into the clone by hand.
6. **Keep the old instance**, stopped, until the incident is closed (it is the evidence), then delete it. Check that
   automated backups and point-in-time recovery are on for the clone, which is now production (the `describe` command
   under [What must be on](#what-must-be-on)).

### What not to do

- **Do not restore over the live instance.** A backup restored onto the live instance overwrites all of its data and
  the log after the point, so you cannot choose a different point if the first one is wrong, and nothing is left to
  compare against.
- Do not point the release at a clone you have not verified, and do not deploy a release whose migration caused the
  incident.
- Do not leave the live instance taking writes after the cutover: they are lost to the clone.

## Namespace recovery with Backup for GKE

The release can be redeployed from git and the signed images at any time
([Standard Deployment](./operations-runbook.md#standard-deployment)), so Backup for GKE is not the only path back:
it restores state that git does not hold (volumes, objects created at run time) and is faster than rebuilding a
namespace by hand. On a cluster other than GKE, the runbook's [Velero](./operations-runbook.md#velero-cluster-backup)
section plays this part.

**A backup plan for the app namespace** (and the database namespace, where the in-cluster database runs) covers:

- the namespace's Kubernetes objects: Deployments, Services, ConfigMaps, Jobs, NetworkPolicies, the Secrets as they
  are in the cluster;
- its volumes' data (`--include-volume-data`), as disk snapshots: the in-cluster database's data and backup PVCs.

**It does not cover:**

- the managed database: Cloud SQL is recovered by [point-in-time recovery](#production-cloud-sql-point-in-time-recovery);
- the external secrets themselves: they live in Secret Manager, and the External Secrets Operator syncs them again
  into the restored namespace;
- the images (they stay in the registry), and the platform's cluster-scoped objects and the bootstrap's RBAC (from
  git, through the admin bootstrap).

### Set up

```bash
gcloud container clusters update "$CLUSTER" --project "$PROJECT_ID" --location "$REGION" \
  --update-addons=BackupRestore=ENABLED
gcloud container backup-restore backup-plans create <app>-daily --project "$PROJECT_ID" --location "$REGION" \
  --cluster="projects/$PROJECT_ID/locations/$REGION/clusters/$CLUSTER" \
  --selected-namespaces=<app-ns>,<db-ns> --include-secrets --include-volume-data \
  --cron-schedule="0 1 * * *" --backup-retain-days=30
```

### Restore

1. Create a restore plan once, for the same namespaces, with a conflict mode that never overwrites a live object
   unless you mean to (`fail-on-conflict` for a drill or a fresh cluster):

   ```bash
   gcloud container backup-restore restore-plans create <app>-restore --project "$PROJECT_ID" --location "$REGION" \
     --backup-plan="projects/$PROJECT_ID/locations/$REGION/backupPlans/<app>-daily" \
     --cluster="projects/$PROJECT_ID/locations/$REGION/clusters/<target-cluster>" \
     --selected-namespaces=<app-ns>,<db-ns> --namespaced-resource-restore-mode=fail-on-conflict \
     --volume-data-restore-policy=restore-volume-data-from-backup
   ```

2. Pick the backup (`gcloud container backup-restore backups list --project "$PROJECT_ID" --location "$REGION" --backup-plan=<app>-daily`)
   and restore it:

   ```bash
   gcloud container backup-restore restores create <app>-restore-<yyyymmdd> --project "$PROJECT_ID" --location "$REGION" \
     --restore-plan=<app>-restore \
     --backup="projects/$PROJECT_ID/locations/$REGION/backupPlans/<app>-daily/backups/<backup>"
   ```

3. Wait for the ExternalSecret to sync and the Pods to turn ready, then run a deploy of the release's current
   revision, so the cluster matches git again.

## The in-cluster database

The dumps are on the backup PVC, which the PostgreSQL Pod mounts read-only at `/backup`; the Pod has `psql`. A dump
is plain SQL without `DROP` statements, so it is restored into an empty database. The current database is set
aside, not dropped, until the restored one passes the app's checks: the data volume needs room for both.

```bash
# 1. Suspend the backup and the restore check for the duration: a dump of a half-restored database
#    would become the newest one.
for c in pg-backup pg-backup-verify; do
  kubectl -n <db-ns> patch cronjob <fullname>-$c -p '{"spec":{"suspend":true}}'
done

#    Suspending stops new runs only: a backup or verify Job already running keeps its connection
#    (step 4 then fails with "being accessed by other users"). Wait until no Job is ACTIVE, or
#    delete the active one:
kubectl -n <db-ns> get jobs
kubectl -n <db-ns> delete job <active-job>        # only if you will not wait for it

# 2. Stop the application (nothing may stay connected to the database for step 4)
kubectl -n <app-ns> scale deployment/<fullname>-backend --replicas=0

# 3. Pick the dump and prove it whole: a readable gzip ending with pg_dump's trailer
kubectl -n <db-ns> exec <fullname>-postgresql-0 -- ls -lt /backup
kubectl -n <db-ns> exec <fullname>-postgresql-0 -- sh -c \
  'gunzip -t /backup/<name>-<timestamp>.sql.gz && gunzip -c /backup/<name>-<timestamp>.sql.gz | tail -n 10'
#    wants: -- PostgreSQL database dump complete

# 4. Set the current database aside and create an empty one
kubectl -n <db-ns> exec <fullname>-postgresql-0 -- sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "ALTER DATABASE \"$POSTGRES_DB\" RENAME TO \"${POSTGRES_DB}_pre_restore\";" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'

# 5. Restore the dump into it (step 3 proved the gzip, so the pipe cannot hide a gunzip error)
kubectl -n <db-ns> exec <fullname>-postgresql-0 -- sh -c \
  'gunzip -c /backup/<name>-<timestamp>.sql.gz | psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

6. Start the application: `kubectl -n <app-ns> scale deployment/<fullname>-backend --replicas=<n>`, with `<n>` the
   HPA's minimum (or `replicas.backend` without the HPA). A deploy does not do it while the HPA owns the replica
   count, the HPA does not scale a Deployment up from zero, and the deploy's checks fail with no backend Pod. The Pods
   stay unready (`schema_behind`) while the restored schema is older than the code
   ([Database, Readiness and liveness](./database.md#readiness-and-liveness)).
7. Deploy the release's current revision: its migration Job brings the restored schema to the release's revision, and
   the deploy's checks prove the service ([Database Migrations](./operations-runbook.md#database-migrations)).
8. Once the checks pass, drop the set-aside database, resume the CronJobs and take a backup by hand
   ([PostgreSQL Backup](./operations-runbook.md#postgresql-backup)):

   ```bash
   kubectl -n <db-ns> exec <fullname>-postgresql-0 -- sh -c 'dropdb -U "$POSTGRES_USER" "${POSTGRES_DB}_pre_restore"'
   for c in pg-backup pg-backup-verify; do
     kubectl -n <db-ns> patch cronjob <fullname>-$c -p '{"spec":{"suspend":false}}'
   done
   ```

**Rolling back** when the restore fails or the checks do not pass: scale the backend to `0`, put the set-aside
database back, then scale it to `<n>` and resume the CronJobs as in step 8.

```bash
kubectl -n <db-ns> exec <fullname>-postgresql-0 -- sh -c \
  'dropdb -U "$POSTGRES_USER" --force "$POSTGRES_DB" && psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres -c "ALTER DATABASE \"${POSTGRES_DB}_pre_restore\" RENAME TO \"$POSTGRES_DB\";"'
```

## Drills

**Once a quarter, a recovery drill**, never on the live data:

1. Restore to a scratch target: a Cloud SQL clone to a timestamp (as above, a new instance) and a release pointed at
   it in a scratch namespace, or a Backup for GKE restore into another cluster, or a dump restored into a scratch
   in-cluster database.
2. Run the app's checks against it: the deploy's checks and the smoke test
   ([Standard Deployment](./operations-runbook.md#standard-deployment)), and the rows you expect.
3. Record the time from the decision to the passing checks against the RTO, the data lost against the RPO, and every
   step the procedure got wrong; fix this page. Then delete the scratch target.

**The weekly restore check** (`<fullname>-pg-backup-verify`, `postgresql.backup.verifySchedule`; the in-cluster
database only) restores the newest dump into a temporary database and fails when there is no dump, the dump is older
than `postgresql.backup.verifyMaxAgeHours` or lacks pg_dump's closing trailer (cut short), the restore fails,
`public` holds no table, a table in `postgresql.backup.verifyTables` is missing, or `alembic_version` holds no row;
it drops the temporary database either way, and one of its own left by a killed run (with no session, and older than
any run may last) at a later start. A failure fires [BackupVerifyFailed](./operations-runbook.md#backupverifyfailed), and a check that stops
succeeding fires [BackupVerifyTooOld](./operations-runbook.md#backupverifytooold). Its age limit is the in-Pod complement of
[BackupTooOld](./operations-runbook.md#backuptooold) and takes that alert's threshold unless set.

It proves that the newest dump is recent, is complete SQL that restores without an error, and holds the schema and a
migration revision. It does not prove:

- that the data is right (it counts no rows outside `alembic_version`);
- that the app runs on the restored database (no app check runs against it);
- the RTO (it restores into the live server, not a new one, and times nothing);
- that the dumps survive the loss of the node or disk they share with the database;
- anything about Cloud SQL or Backup for GKE.

The quarterly drill covers those.

## Ownership

The on-call engineer declares a disaster when data is lost or wrong, or the database or cluster is gone and the
normal fixes cannot bring the service back within the RTO. The declaration opens a P1 incident, run as the runbook's
[Incident Response](./operations-runbook.md#incident-response) describes ([P1 — Service Down](./operations-runbook.md#p1--service-down));
the roles are in its [Key Contacts](./operations-runbook.md#key-contacts). The incident's lead chooses the recovery
point and approves pointing production at a clone.
