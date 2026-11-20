"""The security scripts share ``infra/security/_helpers.sh`` as their runtime.

These tests pin the *contract* of that runtime — the behaviours every script
relies on and the security properties the "pin + verify, never curl|bash" and
"honesty over completeness" rules require. They shell out to tiny bash harnesses
that source the helper, so they exercise the real functions (not a re-impl).

The contract under test:
  - colour degrades off on CI (``NO_COLOR`` / non-TTY / ``dumb``) so logs stay clean;
  - pre-flight names *all* missing commands / env vars and exits non-zero;
  - ``.env`` is *parsed, never sourced* — a value containing command substitution
    must not execute;
  - ``run`` under ``DRY_RUN=true`` prints the argv and executes nothing;
  - ``confirm`` is bypassed by ``LIB_ASSUME_YES`` but refuses a non-interactive
    stdin; ``confirm_break_glass`` is NOT satisfied by ``LIB_ASSUME_YES``;
  - secrets are masked, never printed whole;
  - the ``pass``/``fail``/``summary`` gate returns non-zero when anything failed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # tests/supply_chain -> repo root
HELPER = REPO / "infra/security/_helpers.sh"
# Absolute bash path so ruff S607 (partial executable) is satisfied; the job's
# before_script installs bash, so which() resolves it on CI.
BASH: str = shutil.which("bash") or "/usr/bin/bash"


def helper_source() -> str:
    return f"source {HELPER.as_posix()!r}"


def run_helper(
    harness: str, *, env: dict[str, str] | None = None, stdin: str | None = ""
) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    # stdin="" makes stdin a pipe (non-interactive): `read` sees EOF, not a TTY.
    # check=False: the harness asserts on the helper's exit code, so non-zero is
    # expected data, not a failure to propagate.
    return subprocess.run(
        [BASH, "-c", harness],
        capture_output=True,
        text=True,
        env=full_env,
        input=stdin,
        check=False,
    )


# --- style -------------------------------------------------------------------


def test_helper_exists_and_is_bash() -> None:
    assert HELPER.is_file(), "infra/security/_helpers.sh must exist"
    assert HELPER.read_text().startswith("#!/usr/bin/env bash")


# --- colour degrades on CI ---------------------------------------------------


def test_colour_off_when_no_color_set() -> None:
    r = run_helper(f"{helper_source()}; printf '%s' \"$RED\"", env={"NO_COLOR": "1"})
    assert r.returncode == 0
    assert r.stdout == "", f"RED must be empty under NO_COLOR; got {r.stdout!r}"


def test_colour_off_when_stdout_not_a_tty() -> None:
    # subprocess pipes stdout, so `-t 1` is false — this is the CI case.
    r = run_helper(f"{helper_source()}; printf '%s' \"$RED\"")
    assert r.returncode == 0
    assert r.stdout == "", f"RED must be empty on a non-TTY (CI); got {r.stdout!r}"


def test_log_line_has_no_ansi_escapes_on_ci() -> None:
    r = run_helper(f"{helper_source()}; log 'hello world'", env={"NO_COLOR": "1"})
    assert r.returncode == 0
    assert "\x1b" not in r.stdout, "log output must be escape-free on CI"


def test_timestamp_toggle_suppresses_prefix() -> None:
    with_ts = run_helper(f"{helper_source()}; log 'x'").stdout
    no_ts = run_helper(f"{helper_source()}; log 'x'", env={"LIB_TS": "0"}).stdout
    assert "x" in with_ts
    assert "x" in no_ts
    assert len(no_ts.strip()) < len(with_ts.strip()), "LIB_TS=0 should drop the timestamp prefix"


# --- pre-flight that names all missing ---------------------------------------


def test_need_cmd_names_all_missing_and_fails() -> None:
    r = run_helper(f"{helper_source()}; need_cmd definitely-not-a-cmd another-missing-cmd")
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "definitely-not-a-cmd" in combined
    assert "another-missing-cmd" in combined


def test_require_env_vars_names_all_missing_and_fails() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("NOPE_ALPHA", "NOPE_BETA")}
    r = run_helper(f"{helper_source()}; require_env_vars NOPE_ALPHA NOPE_BETA", env=env)
    assert r.returncode != 0
    combined = r.stdout + r.stderr
    assert "NOPE_ALPHA" in combined
    assert "NOPE_BETA" in combined


# --- safe .env parsing (parse, never source) ---------------------------------


def test_safe_load_env_parses_without_executing(tmp_path: Path) -> None:
    sentinel = tmp_path / "pwned"
    env_file = tmp_path / "secrets.env"
    env_file.write_text(f'GOOD=plainvalue\nEVIL=$(touch {sentinel})\nQUOTED="a b c"\n')
    harness = (
        f"{helper_source()}; "
        "declare -A ENVV=(); "
        f"safe_load_env {env_file.as_posix()!r} ENVV; "
        "printf 'GOOD=[%s]\\n' \"${ENVV[GOOD]:-}\"; "
        "printf 'EVIL=[%s]\\n' \"${ENVV[EVIL]:-}\"; "
        "printf 'QUOTED=[%s]\\n' \"${ENVV[QUOTED]:-}\""
    )
    r = run_helper(harness)
    assert r.returncode == 0, r.stderr
    assert "GOOD=[plainvalue]" in r.stdout
    # The command substitution must be stored literally, never run.
    assert f"EVIL=[$(touch {sentinel})]" in r.stdout
    assert not sentinel.exists(), "safe_load_env must not execute command substitution in values"
    assert "QUOTED=[a b c]" in r.stdout, "one layer of surrounding quotes is stripped"


# --- dry-run runners ----------------------------------------------------------


def test_run_dry_run_executes_nothing_but_shows_argv(tmp_path: Path) -> None:
    marker = tmp_path / "should_not_exist"
    harness = f"DRY_RUN=true; {helper_source()}; run touch {marker.as_posix()!r}"
    r = run_helper(harness)
    assert r.returncode == 0, r.stderr
    assert not marker.exists(), "run under DRY_RUN must execute nothing"
    assert "touch" in r.stdout
    assert "⇢" in r.stdout, "dry-run must print the argv (⇢)"


def test_run_executes_when_not_dry(tmp_path: Path) -> None:
    marker = tmp_path / "should_exist"
    harness = f"{helper_source()}; run touch {marker.as_posix()!r}"
    r = run_helper(harness)
    assert r.returncode == 0, r.stderr
    assert marker.exists(), "run without DRY_RUN must execute"


# --- confirmation (routine vs break-glass) -----------------------------------


def test_confirm_bypassed_by_assume_yes() -> None:
    r = run_helper(f"{helper_source()}; LIB_ASSUME_YES=true; confirm 'proceed?'")
    assert r.returncode == 0, "LIB_ASSUME_YES=true must bypass routine confirm"


def test_confirm_refuses_noninteractive_stdin_without_assume_yes() -> None:
    r = run_helper(f"{helper_source()}; confirm 'proceed?'")
    assert r.returncode != 0, "confirm must refuse a non-interactive stdin unless --yes"


def test_confirm_break_glass_not_satisfied_by_assume_yes() -> None:
    # --yes (LIB_ASSUME_YES) covers routine confirm, but NOT break-glass.
    r = run_helper(
        f"{helper_source()}; LIB_ASSUME_YES=true; "
        "confirm_break_glass ALLOW_UNSIGNED_X 'deploy unsigned?'"
    )
    assert r.returncode != 0, "break-glass must not be satisfied by LIB_ASSUME_YES alone"


def test_confirm_break_glass_opened_by_named_var() -> None:
    r = run_helper(
        f"ALLOW_UNSIGNED_X=1; {helper_source()}; LIB_ASSUME_YES=true; "
        "confirm_break_glass ALLOW_UNSIGNED_X 'deploy unsigned?'"
    )
    assert r.returncode == 0, "setting the named break-glass var opens it"


# --- secret masking -----------------------------------------------------------


def test_mask_never_reveals_value() -> None:
    r = run_helper(f"{helper_source()}; printf '%s' \"$(mask 'supersecretvalue')\"")
    assert r.returncode == 0
    assert "supersecretvalue" not in r.stdout, "mask must not echo the whole secret"
    assert "****" in r.stdout


def test_mask_conn_hides_password() -> None:
    r = run_helper(
        f"{helper_source()}; printf '%s' \"$(mask_conn 'postgresql://app:s3cr3t@db:5432/app')\""
    )
    assert r.returncode == 0
    assert r.stdout == "postgresql://app:****@db:5432/app"
    assert "s3cr3t" not in r.stdout


# --- pass/fail/summary gate ---------------------------------------------------


def test_summary_fails_when_anything_failed() -> None:
    r = run_helper(f"{helper_source()}; pass ok1; fail 'something broke'; summary")
    assert r.returncode != 0, "summary must return non-zero when a step failed"
    assert "something broke" in (r.stdout + r.stderr)


def test_summary_passes_when_nothing_failed() -> None:
    r = run_helper(f"{helper_source()}; pass ok1; pass ok2; summary")
    assert r.returncode == 0, "summary returns zero when nothing failed"


# --- banners render cleanly (no printf arg-reuse duplicates) ------------------


def test_lib_banner_renders_without_duplicate_box_lines() -> None:
    # Each printf format in lib_banner must carry one %s per argument. A missing
    # %s for $RESET makes bash printf reuse the format on the leftover argument,
    # duplicating the "┃ " box line and the "┗" bottom border in every banner.
    r = run_helper(f"{helper_source()}; lib_banner 'TITLE' 'SUBTITLE'", env={"NO_COLOR": "1"})
    assert r.returncode == 0, r.stderr
    lines = r.stdout.split("\n")
    assert lines[0].startswith("┏━ TITLE"), lines
    assert "┃ SUBTITLE" in lines, f"subtitle box line missing: {lines!r}"
    # No duplicate empty "┃ " line, and the "┗" bottom border appears exactly once.
    assert "┃ " not in lines, f"banner has a duplicate empty box line: {lines!r}"
    assert sum(1 for ln in lines if ln.startswith("┗")) == 1, (
        f"bottom border must appear once: {lines!r}"
    )
