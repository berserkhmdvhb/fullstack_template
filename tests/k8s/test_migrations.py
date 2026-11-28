# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The database-migration lifecycle on the kind cluster fst (wiki/database.md, "Proven on kind").

Run with ``make kind-migration-test`` after ``make kind-up kind-deploy``; ``make test`` never
collects it (tests/conftest.py ignores tests/k8s). It talks to fst only, through
.local/kind/kubeconfig, and refuses any other context. It installs a release of its own
(``RELEASE`` in ``NAMESPACE`` and its database namespace, bootstrapped as cluster admin and
removed at the end), deployed as the bootstrap's deployer the way scripts/kind-deploy.sh deploys
(values.local.yaml, the kind secrets file, the images of the kind job's release, ``--wait
--wait-for-jobs --timeout 9m``), so it never disturbs the kind job's main release. The failed
first install and its retry run scripts/ci/deploy-env.sh itself instead (its test-only
``DEPLOY_ENV=kind``, tests/k8s/_kube.py), so ``NAMESPACE`` is the script's ``<release>-kind``; its
images are the kind job's, pushed to the throwaway registry fst-registry for their digests.

The tests are one lifecycle and run in file order (the make target stops at the first failure):
install, a no-op upgrade, two rollbacks, a failed upgrade, a slow migration, the timeouts, the TLS
modes, autogenerate, then a failed first install and its retry on a wiped release. A slow or a
failed migration is the chart's test-only ``tests.migration`` hook. Test manipulation (reading the
database, exec into a Pod, a NetworkPolicy emptied for a moment) runs as cluster admin, as in the
shutdown test.
"""

from __future__ import annotations

import calendar
import json
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.k8s import _kube
from tests.k8s._kube import CONTEXT, GROUP, KUBECONFIG, ROOT, Kube, Pushed, _run, _tool, deployer
from tests.k8s._kube import NAMESPACE as MAIN_NAMESPACE
from tests.k8s._kube import RELEASE as MAIN_RELEASE
from your_service_name.backend.db import schema

CHART = ROOT / "charts" / "your-service-name"
SECRETS = ROOT / ".local" / "kind" / "values.yaml"
# Contains the chart's name, so the fullname is the release name.
RELEASE = "your-service-name-mig"
# deploy-env.sh's <SERVICE_NAME>-<DEPLOY_ENV> for its DEPLOY_ENV=kind (the last case runs it).
NAMESPACE = f"{RELEASE}-kind"
DB_NAMESPACE = f"{NAMESPACE}-db"
DB_POD = f"{RELEASE}-postgresql-0"
DB_HOST = f"{RELEASE}-postgresql.{DB_NAMESPACE}.svc.cluster.local"
BACKEND = f"{RELEASE}-backend"
JOB_PREFIX = f"{RELEASE}-migrate-r"
FAKE_AHEAD = "9999_from_a_newer_release"
# The timeouts this release sets through the chart (config.*: the ConfigMap and the migration Job).
TIMEOUTS = {
    "DB_CONNECT_TIMEOUT_SECONDS": "3",
    "DB_STATEMENT_TIMEOUT_MS": "2000",
    "DB_MIGRATION_LOCK_TIMEOUT_MS": "2000",
}
COMMON = [a for k, v in TIMEOUTS.items() for a in ("--set-string", f"config.{k}={v}")]
# A config change rolls the backend (checksum/config) with no schema change.
ROLL = ["--set-string", "config.LOG_LEVEL=INFO"]
# Seconds: over 150 s (wiki/database.md, "Proven on kind"), inside the Job's
# activeDeadlineSeconds (300).
SLOW = 160
POLL = 0.5  # seconds between EndpointSlice reads
MIN_SAMPLES = 10
# The time (s) a refused probe may take: around the timeout set above, never the default.
STATEMENT_WINDOW = (1.5, 5.0)  # 2 s statement timeout
CONNECT_WINDOW = (2.5, 8.0)  # 3 s connect timeout
LOCK_WINDOW = (1.5, 6.0)  # 2 s lock timeout
CA_IN_POD = "/tmp/test-ca.pem"  # noqa: S108 - in the backend Pod's own /tmp (emptyDir)
OTHER_CA_IN_POD = "/tmp/test-other-ca.pem"  # noqa: S108 - the same

# The backend container has Python and maybe no shell: it compares stdin's SHA-256 with argv[1]
# (exit 75 if it differs, before the script runs) and gives the script the verified stdin.
STDIN_CHECK = (
    "import hashlib, io, sys\n"
    "payload = sys.stdin.buffer.read()\n"
    "if hashlib.sha256(payload).hexdigest() != sys.argv.pop(1):\n"
    "    sys.exit(75)\n"
    "sys.stdin = io.TextIOWrapper(io.BytesIO(payload), encoding='utf-8')\n"
)

# Runs in a backend Pod with its Python and its env (the ConfigMap and the Secret, as the app):
# one connection through the product's own connect arguments, one statement, one JSON line.
PROBE = r"""
import asyncio, json, sys, time
from sqlalchemy import pool, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from your_service_name.backend.db.connect import app_connect_args, migration_connect_args
from your_service_name.backend.settings import Settings

args = json.loads(sys.argv[1])
settings = Settings(**args.get("settings", {}))
url = make_url(settings.database_url)
if args.get("host"):
    url = url.set(host=args["host"])
connect = migration_connect_args if args.get("kind") == "migration" else app_connect_args

async def main():
    engine = create_async_engine(url, poolclass=pool.NullPool, connect_args=connect(settings))
    start, out = time.monotonic(), {"ok": True, "rows": []}
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(args["sql"]))
            if result.returns_rows:
                out["rows"] = [[str(v) for v in row] for row in result]
    except Exception as exc:
        chain, e = [], exc
        while e is not None and len(chain) < 4:
            chain.append(type(e).__name__)
            e = getattr(e, "orig", None) or e.__cause__
        out = {"ok": False, "error": chain, "message": str(exc)[:400]}
    finally:
        await engine.dispose()
    out["seconds"] = round(time.monotonic() - start, 2)
    print(json.dumps(out))

asyncio.run(main())
"""

# Alembic's autogenerate (`alembic check`, through the project's env.py) against the database.
AUTOGEN = r"""
from alembic import command
from alembic.config import Config
from alembic.util.exc import AutogenerateDiffsDetected
from your_service_name.backend.db import run_migrations as runner, schema
from your_service_name.backend.db.connect import migration_connect_args
from your_service_name.backend.settings import Settings

settings = Settings()
cfg = Config(runner._ALEMBIC_CFG_PATH_STR)
cfg.set_main_option("script_location", str(schema.MIGRATIONS_DIR))
cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
cfg.attributes["connect_args"] = migration_connect_args(settings)
try:
    command.check(cfg)
    print("NO-CHANGE")
except AutogenerateDiffsDetected as exc:
    print(f"DIFFS: {exc}")
"""

# GET the chart's tests.apiPath (a route that reads the database) from inside a backend Pod.
SERVE = r"""
import os, sys, urllib.request
url = f"http://127.0.0.1:{os.environ['BACKEND_PORT']}{sys.argv[1]}"
print(urllib.request.urlopen(url, timeout=5).status)
"""
VALUES = yaml.safe_load((CHART / "values.yaml").read_text())
API_PATH = VALUES["tests"]["apiPath"]
DB_USER = VALUES["postgresql"]["auth"]

# Empty the database as the image's bootstrap leaves a new one: the public schema dropped with every
# object in it, then recreated as initdb makes it (owner pg_database_owner, USAGE for PUBLIC, its
# comment). test_a_migration_over_150_s... checks the result against a database the bootstrap's way
# creates (CREATE DATABASE as the chart's user, from template1).
RESET = (
    "DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION pg_database_owner; "
    "GRANT USAGE ON SCHEMA public TO PUBLIC; COMMENT ON SCHEMA public IS 'standard public schema'"
)
# What a fresh database's public schema and settings are: owner, ACL, comment, search_path, the
# objects in it, the default privileges, the schemas and the extensions of the database.
FRESHNESS = """
SELECT n.nspowner::regrole, n.nspacl, obj_description(n.oid, 'pg_namespace'),
  current_setting('search_path'),
  (SELECT count(*) FROM pg_class WHERE relnamespace = n.oid),
  (SELECT count(*) FROM pg_default_acl),
  (SELECT array_agg(nspname ORDER BY nspname) FROM pg_namespace
     WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'),
  (SELECT array_agg(extname ORDER BY extname) FROM pg_extension),
  (SELECT count(*) FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase
     WHERE d.datname = current_database())
FROM pg_namespace n WHERE n.nspname = 'public'
"""


# ---- The cluster, the identities and the deploy -------------------------------------------------


class Cluster:
    """kubectl on the two namespaces, helm as the deployer, and the deploy kind-deploy.sh runs."""

    def __init__(self, work: Path) -> None:
        self.app, self.db = Kube(NAMESPACE), Kube(DB_NAMESPACE)
        self.kubectl = self.app.base[0]
        self.cluster_wide = self.app.base[:-2]  # without -n, for the bootstrap's objects
        self.deployer = deployer()
        helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", CONTEXT]
        self.helm_admin = helm
        self.helm = [*helm, "--kube-as-user", self.deployer, "--kube-as-group", GROUP]
        self.work = work
        # The deploy path's kubectl (scripts/ci/migration-job-lib.sh) as the deployer.
        _kube.deployer_kubectl(work / "bin", self.kubectl)
        self.image_args = self._image_args()

    def _image_args(self) -> list[str]:
        """The kind job's images (its release's values) and the scrape peers, as kind_args sets."""
        main = json.loads(
            _run(
                [
                    *self.helm_admin,
                    "get",
                    "values",
                    MAIN_RELEASE,
                    "-n",
                    MAIN_NAMESPACE,
                    "-o",
                    "json",
                ]
            )
        )
        args: list[str] = []
        for image_id, image in sorted(main.get("images", {}).items()):
            for key in ("registry", "repository", "tag"):
                args += ["--set-string", f"images.{image_id}.{key}={image.get(key, '')}"]
        assert args, f"the release {MAIN_RELEASE} has no image values: run make kind-deploy first"
        selector = "networkPolicy.monitoringNamespaceSelector.kubernetes\\.io/metadata\\.name"
        args += ["--set-string", f"{selector}={NAMESPACE}"]
        args += ["--set-string", "networkPolicy.monitoringPodSelector.metrics-collector=true"]
        return args

    def deploy(self, *extra: str, timeout: str = "9m") -> tuple[int, str]:
        """helm upgrade --install as kind-deploy.sh's deploy(); on a failure, the deploy path's
        report of the newest migration Job (as kind-deploy.sh and deploy-env.sh print it)."""
        cmd = [
            *self.helm,
            "upgrade",
            "--install",
            RELEASE,
            str(CHART),
            "-n",
            NAMESPACE,
            "--server-side=true",
            "--force-conflicts",
        ]
        cmd += ["-f", str(CHART / "values.local.yaml"), "-f", str(SECRETS), *self.image_args]
        cmd += ["--history-max", "5", "--wait", "--wait-for-jobs", "--timeout", timeout]
        cmd += [*COMMON, *extra]
        started = time.time()
        done = subprocess.run(cmd, capture_output=True, text=True, env=_env(), check=False)
        out = done.stdout + done.stderr
        assert "forbidden" not in out.lower(), f"the deployer was refused: {out}"
        if done.returncode:
            out += "\n--- the deploy path's migration Job report\n" + self.report()
        took = time.time() - started
        print(f"deploy {' '.join(extra) or '(base)'}: rc {done.returncode}, {took:.0f}s")
        return done.returncode, out

    def deploy_env(self, pushed: Pushed, *values: Path) -> tuple[int, str]:
        """scripts/ci/deploy-env.sh itself, through its test-only DEPLOY_ENV=kind (_kube.deploy_env:
        as the deployer, the deploy workflow's default HELM_TIMEOUT, the pushed images by digest),
        with this release's timeouts and scrape peers (COMMON's and kind_args's) as values."""
        common = self.work / "common.yaml"
        peers = {
            "monitoringNamespaceSelector": {"kubernetes.io/metadata.name": NAMESPACE},
            "monitoringPodSelector": {"metrics-collector": "true"},
        }
        common.write_text(yaml.safe_dump({"config": TIMEOUTS, "networkPolicy": peers}))
        started = time.time()
        rc, out = _kube.deploy_env(RELEASE, self.work, pushed, common, *values)
        assert "forbidden" not in out.lower(), f"the deployer was refused: {out}"
        took = time.time() - started
        print(f"deploy-env {[v.name for v in values]}: rc {rc}, {took:.0f}s")
        return rc, out

    def lib(self, function: str) -> str:
        """A scripts/ci/migration-job-lib.sh function on this release, kubectl as the deployer."""
        lib = ROOT / "scripts" / "ci" / "migration-job-lib.sh"
        script = f'source "{lib}"; {function} "$1" "$2"'
        env = {**_env(), "PATH": f"{self.work / 'bin'}:{_env()['PATH']}"}
        cmd = ["bash", "-c", script, "_", NAMESPACE, RELEASE]
        done = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
        return done.stdout + done.stderr

    def report(self) -> str:
        return self.lib("migration_job_report_newest")

    def revision(self) -> dict[str, Any]:
        cmd = [*self.helm, "history", RELEASE, "-n", NAMESPACE, "--max", "1", "-o", "json"]
        history: list[dict[str, Any]] = json.loads(_run(cmd))
        return history[-1]

    def job(self, revision: int) -> dict[str, Any]:
        return self.app.get("job", f"{JOB_PREFIX}{revision}")

    def job_pods(self, revision: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.app.get(
            "pods", "-l", f"job-name={JOB_PREFIX}{revision}"
        ).get("items", [])
        return items

    def psql(self, sql: str, database: str = DB_USER["database"]) -> str:
        cmd = ["exec", DB_POD, "--", "psql", "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1"]
        return self.db(*cmd, "-U", DB_USER["user"], "-d", database, "-c", sql).strip()

    def backend_pods(self, *, ready: bool = False) -> list[dict[str, Any]]:
        selector = f"app.kubernetes.io/component=backend,app.kubernetes.io/instance={RELEASE}"
        pods = [
            p
            for p in self.app.get("pods", "-l", selector).get("items", [])
            if not p["metadata"].get("deletionTimestamp")
        ]
        return [p for p in pods if _ready(p)] if ready else pods

    def in_backend(self, script: str, *args: str, stdin: str | None = None) -> str:
        pods = self.backend_pods(ready=True) or self.backend_pods()
        assert pods, "no backend Pod"
        name = pods[0]["metadata"]["name"]
        container = pods[0]["spec"]["containers"][0]["name"]
        cmd = ["exec", "-i", name, "-c", container, "--", "/app/.venv/bin/python", "-I", "-c"]
        if stdin is None:
            return self.app(*cmd, script, *args).strip()
        # The container checks stdin's digest (argv[1]) before the script reads it.
        return _kube.exec_stdin(
            self.app,
            "in_backend",
            stdin,
            lambda digest: [*cmd, STDIN_CHECK + script, digest, *args],
        ).strip()

    def probe(self, sql: str, **args: object) -> dict[str, Any]:
        out = self.in_backend(PROBE, json.dumps({"sql": sql, **args}))
        result: dict[str, Any] = json.loads(out.splitlines()[-1])
        print(f"probe {sql!r} {args}: {result}")
        return result

    def serves(self) -> None:
        """The app answers its API route, which reads the database."""
        assert self.in_backend(SERVE, API_PATH) == "200"

    def ready_endpoints(self) -> int:
        out = self.app(
            "get", "endpointslices", "-l", f"kubernetes.io/service-name={BACKEND}", "-o", "json"
        )
        return sum(
            1
            for s in json.loads(out)["items"]
            for e in s.get("endpoints") or []
            if (e.get("conditions") or {}).get("ready")
        )


def _env() -> dict[str, str]:
    import os  # noqa: PLC0415

    env = {k: v for k, v in os.environ.items() if not k.startswith("HELM_KUBE")}
    env["KUBECONFIG"] = str(KUBECONFIG)
    return env


def _ready(pod: dict[str, Any]) -> bool:
    return any(
        c["type"] == "Ready" and c["status"] == "True"
        for c in pod.get("status", {}).get("conditions", [])
    )


def _restarts(*kubes: Kube) -> dict[str, int]:
    """Containers (init containers included) that restarted, in every Pod of the namespaces."""
    found = {}
    for kube in kubes:
        for pod in kube.get("pods").get("items", []):
            status = pod.get("status", {})
            for c in status.get("initContainerStatuses", []) + status.get("containerStatuses", []):
                if c.get("restartCount"):
                    found[f"{pod['metadata']['name']}/{c['name']}"] = c["restartCount"]
    return found


def _wait(what: str, cond: Callable[[], bool], timeout: float, every: float = 1.0) -> float:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return time.time()
        time.sleep(every)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}")


def _condition(job: dict[str, Any], kind: str) -> bool:
    return any(
        c["type"] == kind and c["status"] == "True"
        for c in job.get("status", {}).get("conditions", [])
    )


@contextmanager
def _endpoints(cluster: Cluster) -> Iterator[list[tuple[float, float, int]]]:
    """Samples (read started, read returned, ready backend endpoints) every POLL seconds while the
    block runs."""
    samples: list[tuple[float, float, int]] = []
    stop = threading.Event()

    def poll() -> None:
        while not stop.is_set():
            started = time.time()
            try:
                n = cluster.ready_endpoints()
            except Exception:  # noqa: BLE001 - a failed read is a missing sample, checked below
                n = -1
            samples.append((started, time.time(), n))
            stop.wait(POLL)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        yield samples
    finally:
        stop.set()
        thread.join()


def _refused(
    result: dict[str, Any], error: str, message: str, within: tuple[float, float] = (0, 60)
) -> None:
    """The probe failed with that error class (in its chain) and message, within the window."""
    assert not result["ok"], result
    assert not error or error in result["error"], result
    assert message in result["message"], result
    low, high = within
    assert low < result["seconds"] < high, result


def _answered(result: dict[str, Any], rows: list[list[str]]) -> None:
    assert result["ok"], result
    assert result["rows"] == rows, result


def _never_empty(samples: list[tuple[float, float, int]]) -> None:
    counts = [n for *_, n in samples]
    gaps = [b[0] - a[0] for a, b in pairwise(samples)]
    summary = f"{len(samples)} samples, min {min(counts)}, max gap {max(gaps):.1f}s"
    print(f"endpoints: {summary}")
    assert len(samples) >= MIN_SAMPLES, summary
    assert -1 not in counts, f"an EndpointSlice read failed: {summary}"
    assert min(counts) > 0, f"the backend Service had no ready endpoint: {summary}"
    assert max(gaps) < 3 * POLL + 2, f"the polling has a hole: {summary}"


def _settled(cluster: Cluster) -> None:
    """Helm returns once the new Pods are ready; the old ones leave after that (their endpoints
    with them), so the endpoint samples run until only the Deployment's replicas remain."""
    want = cluster.app.get("deployment", BACKEND)["spec"]["replicas"]

    def settled() -> bool:
        pods = cluster.app.get("pods", "-l", "app.kubernetes.io/component=backend").get("items", [])
        return len(pods) == want and all(_ready(p) for p in pods)

    _wait("the backend rollout to settle", settled, 180)
    time.sleep(4 * POLL)


def _hooks(cluster: Cluster) -> list[dict[str, Any]]:
    out = _run([*cluster.helm, "get", "hooks", RELEASE, "-n", NAMESPACE])
    return [d for d in yaml.safe_load_all(out) if d]


def _bootstrap() -> str:
    cmd = ["bash", str(ROOT / "scripts" / "k8s-bootstrap.sh"), "local"]
    return _run([*cmd, "--release", RELEASE, "--namespace", NAMESPACE])


def _remove(cluster: Cluster, objects: str) -> None:
    """The release, its kept Jobs and the bootstrap's objects (the namespaces with them)."""
    if _run([*cluster.helm_admin, "status", RELEASE, "-n", NAMESPACE], check=False):
        _run([*cluster.helm, "uninstall", RELEASE, "-n", NAMESPACE, "--wait"], check=False)
    cmd = [*cluster.cluster_wide, "delete", "-f", "-", "--ignore-not-found", "--wait"]
    _run(cmd, objects, check=False)


@pytest.fixture(scope="module")
def cluster() -> Iterator[Cluster]:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    with tempfile.TemporaryDirectory() as work:
        c = Cluster(Path(work))
        objects = _bootstrap()
        _remove(c, objects)  # what an interrupted run left behind
        _run([*c.cluster_wide, "apply", "-f", "-"], objects)
        try:
            yield c
        finally:
            _remove(c, objects)


@pytest.fixture(scope="module")
def pushed(cluster: Cluster) -> Iterator[Pushed]:
    """The kind job's images in the throwaway registry, for the cases deploy-env.sh deploys."""
    tls = _kube.registry_tls(cluster.work / "tls")
    with _kube.registry(cluster.app, tls) as host:
        yield _kube.push_release_images(host, tls, "fst-migrations")


# ---- The lifecycle, in order ---------------------------------------------------------------------


def test_first_install_with_the_in_cluster_database(cluster: Cluster) -> None:
    rc, out = cluster.deploy()
    assert rc == 0, out
    rev = cluster.revision()
    assert (rev["revision"], rev["status"]) == (1, "deployed")
    job = cluster.job(1)
    assert _condition(job, "Complete"), job.get("status")
    assert len(cluster.job_pods(1)) == 1  # one attempt
    assert _restarts(cluster.app, cluster.db) == {}
    # The Job is a plain release resource: in the manifest, and no hook but helm test's own.
    hooks = _hooks(cluster)
    assert all(h["metadata"]["annotations"]["helm.sh/hook"] == "test" for h in hooks), hooks
    assert not [
        h for h in hooks if h["kind"] == "Job" and h["metadata"]["name"].startswith(JOB_PREFIX)
    ]
    manifest = _run([*cluster.helm, "get", "manifest", RELEASE, "-n", NAMESPACE])
    assert f"name: {JOB_PREFIX}1\n" in manifest
    assert cluster.psql("SELECT version_num FROM alembic_version") == schema.load_revisions().head
    cluster.serves()


def test_a_no_op_upgrade_runs_a_new_job_and_never_empties_the_endpoints(cluster: Cluster) -> None:
    before = {p["metadata"]["uid"] for p in cluster.backend_pods()}
    with _endpoints(cluster) as samples:
        rc, out = cluster.deploy(*ROLL)
        _settled(cluster)
    assert rc == 0, out
    assert (cluster.revision()["revision"], cluster.revision()["status"]) == (2, "deployed")
    assert _condition(cluster.job(2), "Complete")
    assert "already at head" in cluster.app("logs", f"job/{JOB_PREFIX}2", "-c", "migrate")
    after = {p["metadata"]["uid"] for p in cluster.backend_pods()}
    assert after, "no backend Pod after the upgrade"
    assert not after & before, "the upgrade did not roll the backend"
    _never_empty(samples)
    assert _restarts(cluster.app, cluster.db) == {}


def test_a_rollback_within_the_ttl_adopts_the_kept_job_without_rerunning_it(
    cluster: Cluster,
) -> None:
    """Revision 1's Job still exists (TTL 24 h, resource-policy keep): the rollback renders the
    same Job, Helm takes it over unchanged (its spec is immutable), and it does not run again."""
    kept = cluster.job(1)
    assert _condition(kept, "Complete")
    with _endpoints(cluster) as samples:
        rc, out = _rollback(cluster, 1)
        _settled(cluster)
    assert rc == 0, out
    assert (cluster.revision()["revision"], cluster.revision()["status"]) == (3, "deployed")
    job = cluster.job(1)
    assert job["metadata"]["uid"] == kept["metadata"]["uid"]
    assert job["status"].get("completionTime") == kept["status"].get("completionTime")
    assert len(cluster.job_pods(1)) == 1  # not run again
    assert cluster.job(2), "revision 2's Job (keep) was deleted by the rollback"
    _never_empty(samples)
    cluster.serves()


def test_a_rollback_after_the_ttl_recreates_the_job_which_finds_the_database_ahead(
    cluster: Cluster,
) -> None:
    """The TTL controller removes revision 2's Job (its TTL lowered to 1 s: the field is mutable),
    and the database is stamped with a revision this code does not know, as a newer release would
    leave it. The rollback recreates the Job; it runs, logs that the database is ahead, exits 0."""
    old = cluster.job(2)
    cluster.app(
        "patch",
        "job",
        f"{JOB_PREFIX}2",
        "--type=merge",
        "-p",
        '{"spec":{"ttlSecondsAfterFinished":1}}',
    )
    _wait("the TTL controller to remove the Job", lambda: not cluster.job(2), 120)
    head = schema.load_revisions().head
    cluster.psql(f"UPDATE alembic_version SET version_num = '{FAKE_AHEAD}'")  # noqa: S608
    try:
        with _endpoints(cluster) as samples:
            rc, out = _rollback(cluster, 2)
            _settled(cluster)
        assert rc == 0, out
        job = cluster.job(2)
        assert job["metadata"]["uid"] != old["metadata"]["uid"]
        assert _condition(job, "Complete")
        log = cluster.app("logs", f"job/{JOB_PREFIX}2", "-c", "migrate")
        assert "database is ahead of this release; nothing to do" in log
        assert cluster.psql("SELECT version_num FROM alembic_version") == FAKE_AHEAD  # no downgrade
        _never_empty(samples)  # an unknown (newer) revision counts as ready
        cluster.serves()
    finally:
        cluster.psql(f"UPDATE alembic_version SET version_num = '{head}'")  # noqa: S608


def _rollback(cluster: Cluster, revision: int) -> tuple[int, str]:
    cmd = [
        *cluster.helm,
        "rollback",
        RELEASE,
        str(revision),
        "-n",
        NAMESPACE,
        "--server-side=true",
        "--force-conflicts",
    ]
    cmd += ["--wait", "--wait-for-jobs", "--timeout", "9m", "--history-max", "5"]
    done = subprocess.run(cmd, capture_output=True, text=True, env=_env(), check=False)
    return done.returncode, done.stdout + done.stderr


def test_a_failed_upgrade_fails_the_release_and_the_old_pods_keep_serving(
    cluster: Cluster,
) -> None:
    """The migration fails on purpose (the Job's only change): Helm reports the failure, the
    running Pods are left alone and keep serving, and the deploy path prints the Job's logs."""
    before = {p["metadata"]["uid"] for p in cluster.backend_pods()}
    with _endpoints(cluster) as samples:
        rc, out = cluster.deploy(*ROLL, "--set", "tests.migration.fail=true")
    print(out)
    assert rc != 0
    rev = cluster.revision()
    assert (rev["revision"], rev["status"]) == (5, "failed")
    job = cluster.job(5)
    assert _condition(job, "Failed"), job.get("status")
    assert len(cluster.job_pods(5)) == job["spec"]["backoffLimit"] + 1  # every retry ran
    # The deploy path's report: the Job's status line and its Pods' logs.
    report = out.split("--- the deploy path's migration Job report", 1)[1]
    assert f"migration job: {JOB_PREFIX}5|" in report
    assert "MIGRATION_TEST_FAIL: failing on purpose" in report
    assert {p["metadata"]["uid"] for p in cluster.backend_pods(ready=True)} == before
    _never_empty(samples)
    cluster.serves()
    assert (
        _restarts(cluster.app, cluster.db) == {}
    )  # the failed attempts are new Pods, not restarts


def test_a_migration_over_150_s_runs_without_restarts_and_holds_readiness(
    cluster: Cluster,
) -> None:
    """The database is emptied (RESET: the same as a fresh one, checked against a database created
    the bootstrap's way), then an upgrade whose migration sleeps SLOW seconds first: the backend
    stays unready until the migration has committed, then becomes ready; nothing restarts
    (liveness and startup probe the process, not the schema)."""
    reference = "freshness_reference"
    cluster.psql(f"DROP DATABASE IF EXISTS {reference}")
    cluster.psql(f"CREATE DATABASE {reference}")
    try:
        fresh = cluster.psql(FRESHNESS, database=reference)
    finally:
        cluster.psql(f"DROP DATABASE {reference}")
    cluster.psql(RESET)
    assert cluster.psql(FRESHNESS) == fresh, "the reset database differs from a fresh one"
    print(f"reset database = fresh one: {fresh}")
    _wait("the backend to go unready (schema missing)", lambda: not cluster.ready_endpoints(), 60)
    pods = {p["metadata"]["uid"] for p in cluster.backend_pods()}
    with _endpoints(cluster) as samples:
        rc, out = cluster.deploy(*ROLL, "--set", f"tests.migration.delaySeconds={SLOW}")
    assert rc == 0, out + _migrate_logs(cluster)
    rev = cluster.revision()["revision"]
    job = cluster.job(rev)
    start = _ts(job["status"]["startTime"])
    done = _ts(job["status"]["completionTime"])
    print(f"migration Job ran {done - start:.0f}s")
    assert done - start > SLOW - 10  # over 150 s
    assert len(cluster.job_pods(rev)) == 1
    log = cluster.app("logs", "--timestamps", f"job/{JOB_PREFIX}{rev}", "-c", "migrate")
    assert "Database is missing; upgrading to head" in log  # a first migration
    _unready_until(samples, _committed(log))
    assert {p["metadata"]["uid"] for p in cluster.backend_pods()} == pods  # same Pods, now ready
    assert _restarts(cluster.app, cluster.db) == {}
    assert cluster.psql("SELECT version_num FROM alembic_version") == schema.load_revisions().head
    cluster.serves()


def _ts(stamp: str) -> float:
    return calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))


def _committed(log: str) -> float:
    """When the migrate container logged ``Migrations applied.`` (run_migrations.py logs it once
    alembic's upgrade has returned, so after its transaction committed), from the
    ``kubectl logs --timestamps`` prefix (RFC 3339, nanoseconds)."""
    line = next((x for x in log.splitlines() if "Migrations applied." in x), None)
    assert line, f"the migration never logged its commit:\n{log}"
    return datetime.fromisoformat(line.split(" ", 1)[0]).timestamp()


def _unready_until(samples: list[tuple[float, float, int]], committed: float) -> None:
    """No read provably saw a ready backend endpoint before the migration committed: a sample is a
    violation only when the read that returned n > 0 returned before ``committed`` (1 s slack for
    timestamp resolution). The first ready observation is at or after it by the same rule."""
    before = [n for _, returned, n in samples if returned < committed - 1]
    ready = [returned for _, returned, n in samples if n > 0]
    assert before, "no sample while the migration ran"
    assert max(before) == 0, "a backend endpoint was ready before the migration committed"
    assert ready, "the backend never became ready"
    assert min(ready) >= committed - 1, "ready before the migration committed"


def _migrate_logs(cluster: Cluster) -> str:
    """The migrate container's logs, with kubectl's stderr, from every Pod of the newest migration
    Job (each failed attempt). It never raises: it runs inside a failing assertion's message."""
    try:
        pods = _kube._run_both([*cluster.app.base, "get", "pods", "-l", "job-name", "-o", "json"])
        if pods.returncode:
            return f"\n--- could not list the Job Pods: {pods.stderr.strip()}"
        jobs: dict[int, list[str]] = {}
        for pod in json.loads(pods.stdout).get("items", []):
            job = pod["metadata"]["labels"]["job-name"]
            if job.startswith(JOB_PREFIX) and job[len(JOB_PREFIX) :].isdigit():
                jobs.setdefault(int(job[len(JOB_PREFIX) :]), []).append(pod["metadata"]["name"])
        if not jobs:
            return "\n--- no migration Job Pod is left"
        logs = []
        for name in jobs[max(jobs)]:
            done = _kube._run_both(
                [*cluster.app.base, "logs", name, "-c", "migrate", "--timestamps"]
            )
            logs.append(f"\n--- migrate logs of {name}\n{done.stdout}{done.stderr}")
        return "".join(logs)
    except Exception as exc:  # noqa: BLE001 - the deploy output must still reach the report
        return f"\n--- could not read the migrate logs: {type(exc).__name__}: {exc}"


def test_the_chart_s_timeouts_take_effect_against_the_in_cluster_database(
    cluster: Cluster,
) -> None:
    """Through the backend's own env (the ConfigMap) and connect arguments: the statement timeout
    cancels a slow query; the connect timeout ends a connect to the database's Service when its
    NetworkPolicy drops the traffic (it never answers); the migration's lock timeout ends a wait
    on a locked table."""
    job = cluster.job(cluster.revision()["revision"])
    migrate = next(
        c for c in job["spec"]["template"]["spec"]["containers"] if c["name"] == "migrate"
    )
    env = {e["name"]: e.get("value") for e in migrate["env"]}
    assert env["DB_CONNECT_TIMEOUT_SECONDS"] == TIMEOUTS["DB_CONNECT_TIMEOUT_SECONDS"]
    assert env["DB_MIGRATION_LOCK_TIMEOUT_MS"] == TIMEOUTS["DB_MIGRATION_LOCK_TIMEOUT_MS"]

    shown = cluster.probe("SHOW statement_timeout")
    assert shown["rows"] == [["2s"]], shown
    slow = cluster.probe("SELECT pg_sleep(6)")
    _refused(slow, "QueryCanceledError", "statement timeout", STATEMENT_WINDOW)

    # The database's ingress allow emptied: its Pod stays isolated, so the SYN is dropped.
    policy, path = f"{RELEASE}-postgresql-access", "/spec/ingress"
    saved = cluster.db.get("networkpolicy", policy)["spec"]["ingress"]

    def ingress(rules: list[Any]) -> None:
        patch = json.dumps([{"op": "replace", "path": path, "value": rules}])
        cluster.db("patch", "networkpolicy", policy, "--type=json", "-p", patch)

    ingress([])
    try:
        time.sleep(3)  # the CNI's policy controller applies it
        stalled = cluster.probe("SELECT 1", kind="migration")
    finally:
        ingress(saved)
    _refused(stalled, "TimeoutError", "", CONNECT_WINDOW)

    # alembic_version: the one table every schema has (the migration takes it first).
    lock_sql = "BEGIN; LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE; "
    lock_sql += "SELECT pg_sleep(10); COMMIT;"
    psql = ["psql", "-X", "-q", "-U", DB_USER["user"], "-d", DB_USER["database"], "-c", lock_sql]
    holder = subprocess.Popen(
        [*cluster.db.base, "exec", DB_POD, "--", *psql],
        env=_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        held = "SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation "
        held += "WHERE c.relname = 'alembic_version' AND l.mode = 'AccessExclusiveLock' "
        held += "AND l.granted"
        _wait("the test's lock", lambda: cluster.psql(held) == "1", 30, every=0.3)
        locked = cluster.probe("LOCK TABLE alembic_version IN ACCESS SHARE MODE", kind="migration")
    finally:
        holder.wait(30)
    _refused(locked, "LockNotAvailableError", "lock timeout", LOCK_WINDOW)
    cluster.serves()


def _pki(work: Path) -> None:
    """A CA, a server certificate for the database's Service name signed by it, and a second CA."""

    def openssl(*args: str) -> None:
        subprocess.run(["openssl", *args], cwd=work, check=True, capture_output=True)  # noqa: S607

    ca = ["-addext", "basicConstraints=critical,CA:TRUE"]
    ca += ["-addext", "keyUsage=critical,keyCertSign,cRLSign"]
    for name in ("ca", "other-ca"):
        openssl(
            "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", f"/CN=kind-{name}", "-keyout", f"{name}.key", "-out", f"{name}.pem", *ca,
        )  # fmt: skip
    (work / "server.ext").write_text(
        f"subjectAltName=DNS:{DB_HOST}\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n"
    )
    openssl(
        "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=kind-test-db",
        "-keyout", "server.key", "-out", "server.csr",
    )  # fmt: skip
    openssl(
        "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
        "-days", "1", "-out", "server.crt", "-extfile", "server.ext",
    )  # fmt: skip


def _ssl_in_use(
    cluster: Cluster, mode: str, settings: dict[str, str] | None = None, host: str = ""
) -> dict[str, Any]:
    sql = "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
    return cluster.probe(sql, settings={"DB_SSLMODE": mode, **(settings or {})}, host=host)


def test_each_tls_mode_behaves_as_documented_against_the_in_cluster_database(
    cluster: Cluster,
) -> None:
    """wiki/database.md, TLS to the database. Against the chart's PostgreSQL as it ships (no TLS):
    disable connects in plaintext, and every other mode refuses rather than fall back. Then the
    test turns TLS on in that PostgreSQL (a certificate for its Service name from a test CA):
    require encrypts without verifying, verify-ca checks the chain and not the name, verify-full
    checks both, a wrong CA or the system trust store is refused; and the chart's own path
    (config.DB_SSLMODE=verify-full with database.tls.caSecret) migrates and serves over TLS."""
    plain = _ssl_in_use(cluster, "disable")
    _answered(plain, [["False"]])
    for mode in ("require", "verify-ca", "verify-full"):
        refused = _ssl_in_use(cluster, mode)
        _refused(refused, "", "rejected SSL upgrade")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _pki(work)
        for name, mode in (("server.crt", "644"), ("server.key", "600")):
            write = f'umask 077; cat > "$PGDATA/{name}" && chmod {mode} "$PGDATA/{name}"'
            shell = ["exec", "-i", DB_POD, "--", "sh", "-c", _kube.SH_STDIN_CHECK + write, "sh"]
            payload = (work / name).read_text()

            def command(digest: str, shell: list[str] = shell) -> list[str]:
                return [*shell, digest]

            _kube.exec_stdin(cluster.db, "TLS files", payload, command)
        for name in ("ca.pem", "other-ca.pem"):
            save = f"import sys; open('/tmp/test-{name}', 'w').write(sys.stdin.read())"
            cluster.in_backend(save, stdin=(work / name).read_text())
        ca_pem = (work / "ca.pem").read_text()
    cluster.psql("ALTER SYSTEM SET ssl = on")
    cluster.psql("SELECT pg_reload_conf()")
    _wait("PostgreSQL with TLS on", lambda: cluster.psql("SHOW ssl") == "on", 30)

    short = f"{RELEASE}-postgresql.{DB_NAMESPACE}"  # resolves, but is not in the certificate
    ca, other = {"DB_SSLROOTCERT": CA_IN_POD}, {"DB_SSLROOTCERT": OTHER_CA_IN_POD}
    # (mode, CA file, host, ssl in use; None: the certificate is refused)
    cases: list[tuple[str, dict[str, str], str, str | None]] = [
        ("disable", {}, "", "False"),
        ("require", {}, "", "True"),
        ("verify-ca", ca, "", "True"),
        ("verify-ca", ca, short, "True"),
        ("verify-ca", other, "", None),
        ("verify-full", ca, "", "True"),
        ("verify-full", ca, short, None),
        ("verify-full", {}, "", None),  # the image's system trust store
    ]
    for mode, root, host, want in cases:
        got = _ssl_in_use(cluster, mode, root, host)
        if want is None:
            _refused(got, "SSLCertVerificationError", "")
        else:
            _answered(got, [[want]])

    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "test-db-ca", "namespace": NAMESPACE},
        "stringData": {"ca.pem": ca_pem},
    }
    cluster.app("apply", "-f", "-", stdin=json.dumps(secret))
    tls = ["--set-string", "config.DB_SSLMODE=verify-full"]
    tls += [
        "--set",
        "database.tls.caSecret.name=test-db-ca",
        "--set",
        "database.tls.caSecret.key=ca.pem",
    ]
    rc, out = cluster.deploy(*ROLL, *tls)
    assert rc == 0, out
    assert _condition(cluster.job(cluster.revision()["revision"]), "Complete")
    user = DB_USER["user"]
    sessions = "SELECT bool_and(s.ssl), count(*) FROM pg_stat_ssl s JOIN pg_stat_activity a "
    sessions += f"USING (pid) WHERE a.usename = '{user}' AND a.pid <> pg_backend_pid()"
    _settled(cluster)  # the old Pods and their plaintext sessions are gone
    cluster.serves()  # opens app connections over TLS
    _wait("only TLS sessions of the app", lambda: cluster.psql(sessions).startswith("t|"), 60)
    rc, out = cluster.deploy(*ROLL)  # back to the chart's default (disable)
    assert rc == 0, out
    assert _restarts(cluster.app, cluster.db) == {}


def test_autogenerate_finds_no_change_against_the_migrated_database(cluster: Cluster) -> None:
    """The models and the migrations agree: `alembic check` (autogenerate) on the live schema."""
    assert cluster.in_backend(AUTOGEN).splitlines()[-1] == "NO-CHANGE"


def test_a_failed_first_install_then_a_retry_succeeds_with_no_manual_cleanup(
    cluster: Cluster, pushed: Pushed
) -> None:
    """On a wiped release (uninstalled, its kept Jobs and its database volume deleted), deployed by
    scripts/ci/deploy-env.sh itself: the first install's migration fails on purpose, and
    --rollback-on-failure uninstalls the release at the timeout, leaving the Failed Job (resource-
    policy keep), whose logs the report prints. The retry, the same deploy without the failure,
    deletes that Job itself (migration_job_purge_failed: no deployed revision) and installs
    revision 1, whose Job migrates the untouched database. Nothing is removed by hand in between."""
    _run([*cluster.helm, "uninstall", RELEASE, "-n", NAMESPACE, "--wait"])
    cluster.app("delete", "jobs", "-l", f"app.kubernetes.io/instance={RELEASE}", "--wait")
    _wait("the PostgreSQL Pod to be gone", lambda: not cluster.db.get("pod", DB_POD), 180)
    cluster.db("delete", "pvc", "-l", f"app.kubernetes.io/instance={RELEASE}", "--wait")

    fail = cluster.work / "fail.yaml"
    fail.write_text("tests: {migration: {fail: true}}\n")
    rc, out = cluster.deploy_env(pushed, fail)
    print(out)
    assert rc != 0
    status = [*cluster.helm, "status", RELEASE, "-n", NAMESPACE]
    assert not _run(status, check=False), "the failed first install was not uninstalled"
    failed = cluster.job(1)
    assert _condition(failed, "Failed"), failed.get("status")
    report = out.split(f"::group::migration Job of {RELEASE} in kind (the deploy failed)", 1)[1]
    assert f"migration job: {JOB_PREFIX}1|" in report
    assert "MIGRATION_TEST_FAIL: failing on purpose" in report

    rc, out = cluster.deploy_env(pushed)
    print(out)
    assert rc == 0, out
    assert f"deleting the Failed migration Job {JOB_PREFIX}1 of {RELEASE}" in out
    rev = cluster.revision()
    assert (rev["revision"], rev["status"]) == (1, "deployed")
    job = cluster.job(1)
    assert job["metadata"]["uid"] != failed["metadata"]["uid"]
    assert _condition(job, "Complete")
    log = cluster.app("logs", f"job/{JOB_PREFIX}1", "-c", "migrate")
    assert "Database is missing; upgrading to head" in log  # the failed install left it untouched
    assert cluster.psql("SELECT version_num FROM alembic_version") == schema.load_revisions().head
    assert _restarts(cluster.app, cluster.db) == {}
    cluster.serves()
