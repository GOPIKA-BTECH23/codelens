"""Pure-logic tests for the worker pieces (no database)."""

import json
import logging
from decimal import Decimal

from app.core.logging import JsonFormatter, job_id_var, run_id_var
from app.db.models.enums import RunStatus
from app.services.runs import ALLOWED_SOURCES, TERMINAL
from tests.app_utils import make_settings


def test_terminal_run_states_have_no_way_out() -> None:
    for target, sources in ALLOWED_SOURCES.items():
        assert not (sources & TERMINAL), f"{target} may not be entered from a terminal state"


def test_every_run_status_is_either_terminal_or_active() -> None:
    assert {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.SKIPPED,
        RunStatus.SUPERSEDED,
    } == TERMINAL
    assert set(RunStatus) - TERMINAL == {RunStatus.QUEUED, RunStatus.RUNNING}


def test_succeeded_can_only_come_from_running() -> None:
    assert ALLOWED_SOURCES[RunStatus.SUCCEEDED] == {RunStatus.RUNNING}


def test_log_lines_carry_job_and_run_ids() -> None:
    job_token = job_id_var.set("job-1")
    run_token = run_id_var.set("run-1")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
        line = json.loads(JsonFormatter().format(record))
    finally:
        job_id_var.reset(job_token)
        run_id_var.reset(run_token)
    assert line["job_id"] == "job-1" and line["run_id"] == "run-1"


def test_log_lines_omit_ids_outside_a_job() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    line = json.loads(JsonFormatter().format(record))
    assert "job_id" not in line and "run_id" not in line


def test_worker_settings_defaults() -> None:
    settings = make_settings()
    assert settings.run_worker_in_api is False
    assert settings.daily_cost_cap_usd == Decimal("5")
    assert settings.worker_concurrency == 2
