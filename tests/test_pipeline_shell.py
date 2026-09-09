"""Offline marker/guard tests for scripts/pipeline.sh (SDD-F WU1).

Spec NGT-R1/TLG-R1 -> NGT-S1..S4 / TLG-S1..S4, design D1-D3/D8. The nightly
dbt exec must be a single ``dbt build --select`` invocation (models AND their
tests for the exact select, one docker exec; a model/test failure folds into
the existing failure branch -> dbt_status=failed -> failed verdict) while the
``pipeline_run_steps`` series keeps step_name ``dbt_run``. Telegram keys read
exactly TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID from root ./.env when unset (env
wins) via a file-guarded, key-targeted, export-free fallback.

These are file-marker tests (house precedent: test_wipe_sim.py:896/:912
``read_text`` scanning): the pipeline script is host-shell behavior that the
CI pytest-only suite must never execute, so the assertions pin the authored
guard lines instead (design D8; bash -n proves syntax, not semantics).
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE_SH = _REPO_ROOT / "scripts" / "pipeline.sh"
_TEXT = _PIPELINE_SH.read_text(encoding="utf-8")


def _assert_absent(text: str, fragment: str, label: str) -> None:
    assert fragment not in text, (
        f"{label}: forbidden fragment {fragment!r} present in {_PIPELINE_SH.name}:\n{text}"
    )


def test_nightly_exec_runs_dbt_build_over_the_select():
    """NGT-S1: the nightly exec is one ``dbt build`` over ``${dbt_select}``."""
    assert 'dbt build --select "${dbt_select}"' in _TEXT, (
        "nightly exec must run dbt build --select over ${dbt_select}"
    )
    _assert_absent(_TEXT, "dbt run --select", "NGT-S1")


def test_nightly_step_name_stays_dbt_run():
    """NGT-S1: pipeline_run_steps keeps step_name dbt_run (D2, series intact)."""
    assert 'pipeline_start_step "$run_id" "dbt_run"' in _TEXT, (
        "step_name must stay dbt_run so the pipeline_run_steps series is not split"
    )


def test_nightly_build_failure_reason_is_truthful():
    """NGT-S2: the failure reason string names the build subcommand."""
    assert "dbt build falló" in _TEXT, "failure reason must read 'dbt build falló'"
    _assert_absent(_TEXT, "dbt run falló", "NGT-S2")


def test_telegram_env_guard_sits_after_both_defaults():
    """TLG-S1/S3: the ./.env fallback is file-guarded and placed below both
    TELEGRAM_* defaults (host env wins; absent file keeps the silent skip)."""
    defaults_end = _TEXT.index('TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"')
    guard_at = _TEXT.index("[ -f ./.env ]")
    assert guard_at > defaults_end, (
        ".env guard must come after both TELEGRAM defaults (line-order, D3)"
    )


def test_telegram_fallback_reads_only_the_two_keys():
    """TLG-S2: unset keys are extracted with exact-key anchored sed only."""
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert f"sed -n 's/^{key}=//p' ./.env" in _TEXT, (
            f"fallback must extract {key} with an anchored key-targeted sed"
        )
        assert f'[ -z "${key}" ] &&' in _TEXT, (
            f"fallback must only read {key} when it is unset (env wins)"
        )


def test_telegram_fallback_never_pollutes_the_process_env():
    """TLG-S4: no set -a, no full source, no export of the Telegram keys.

    Scans executable lines only (comment prose may legitimately name the
    forbidden constructs while documenting their absence).
    """
    executable_lines = [
        ln for ln in _TEXT.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    for fragment in ("set -a", "export TELEGRAM", "source ./.env", ". ./.env"):
        offenders = [ln for ln in executable_lines if fragment in ln]
        assert offenders == [], f"TLG-S4: forbidden construct {fragment!r} in:\n{offenders}"
