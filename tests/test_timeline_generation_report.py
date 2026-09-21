"""The timeline generation cycle reports what it did, and the scheduler turns
that into the admin push only after the cycle succeeded.

generate_for_chain is exercised with the DB lookup, the LLM and TTS faked: the
interesting behaviour is which outcome lands in which counter.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import build_story_timelines as build
from scripts import run_timeline_scheduler as scheduler
from scripts.build_story_timelines import GenerationReport
from app.services.timeline_narrative import TimelineNarrativeError

CHAIN = frozenset({1, 2})
BY_ID = {
    1: SimpleNamespace(id=1, first_seen_at=datetime(2026, 9, 20, tzinfo=timezone.utc)),
    2: SimpleNamespace(id=2, first_seen_at=datetime(2026, 9, 21, tzinfo=timezone.utc)),
}


def _row(**overrides):
    fields = dict(
        anchor_cluster_id=2, is_editorial_pick=False, narrative_generated_at=None,
        cluster_ids=None, coherent=None, title=None, spoken_script_hash=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def fakes(monkeypatch):
    """Returns a holder the test sets .existing / .narrative on."""
    state = SimpleNamespace(existing=None, narrative={"coherent": True, "title": "Manipur talks"})

    async def find_existing(session, chain_ids, anchor):
        return state.existing

    async def generate_narrative(session, members):
        if isinstance(state.narrative, Exception):
            raise state.narrative
        return state.narrative

    monkeypatch.setattr(build, "_find_existing_row", find_existing)
    monkeypatch.setattr(build, "generate_narrative", generate_narrative)
    # A narrative carrying no spoken_script never reaches TTS.
    return state


async def _generate(report):
    session = MagicMock()
    return await build.generate_for_chain(session, CHAIN, False, BY_ID, dry_run=False, report=report)


class TestGenerationReport:
    async def test_coherent_narrative_is_reported_by_title(self, fakes):
        report = GenerationReport()
        await _generate(report)
        assert report.titles == ["Manipur talks"]
        assert (report.rejected, report.failed) == (0, 0)

    async def test_incoherent_narrative_counts_as_rejected(self, fakes):
        fakes.narrative = {"coherent": False, "title": None}
        report = GenerationReport()
        await _generate(report)
        assert report.titles == []
        assert report.rejected == 1

    async def test_generation_error_counts_as_failed(self, fakes):
        fakes.narrative = TimelineNarrativeError("claude down")
        report = GenerationReport()
        await _generate(report)
        assert report.failed == 1
        assert report.titles == [] and report.rejected == 0

    async def test_unchanged_chain_reports_nothing(self, fakes):
        """No LLM call means nothing to announce — this is what keeps a quiet
        day from producing a push."""
        fakes.existing = _row(
            narrative_generated_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
            cluster_ids=[1, 2], coherent=True)
        report = GenerationReport()
        await _generate(report)
        assert report.titles == [] and (report.rejected, report.failed) == (0, 0)

    async def test_report_is_optional(self, fakes):
        """Other callers (a REPL, a one-off script) needn't pass one."""
        anchor, coherent = await _generate(None)
        assert (anchor, coherent) == (2, True)


class TestSchedulerNotification:
    async def test_notifies_with_the_cycles_report_after_success(self, monkeypatch):
        report = GenerationReport(titles=["Manipur talks"], rejected=1, failed=0, unfilled=2)
        notified = []

        async def run_generation():
            return report

        async def notify_generated(r):
            notified.append(r)

        monkeypatch.setattr(scheduler, "run_generation", run_generation)
        monkeypatch.setattr(scheduler, "notify_generated", notify_generated)

        await scheduler.run_once()
        assert notified == [report]

    async def test_a_crashed_cycle_sends_the_failure_alert_not_the_summary(self, monkeypatch):
        alerts, notified = [], []

        async def run_generation():
            raise RuntimeError("db gone")

        async def notify_generated(r):
            notified.append(r)

        async def notify_admin_failure(source, error):
            alerts.append(source)

        monkeypatch.setattr(scheduler, "run_generation", run_generation)
        monkeypatch.setattr(scheduler, "notify_generated", notify_generated)
        monkeypatch.setattr("app.services.admin_notify.notify_admin_failure", notify_admin_failure)

        await scheduler.run_once()
        assert alerts == ["timeline_scheduler"]
        assert notified == []

    async def test_notification_trouble_is_not_a_generation_failure(self, monkeypatch):
        """The cycle has already committed; a broken notifier must neither
        raise out of run_once nor trigger the crash alert."""
        alerts = []

        async def run_generation():
            return GenerationReport(titles=["Manipur talks"])

        class ExplodingSession:
            async def __aenter__(self):
                raise RuntimeError("no connection")

            async def __aexit__(self, *exc):
                return False

        async def notify_admin_failure(source, error):
            alerts.append(source)

        monkeypatch.setattr(scheduler, "run_generation", run_generation)
        monkeypatch.setattr("app.database.AsyncSessionLocal", lambda: ExplodingSession())
        monkeypatch.setattr("app.services.admin_notify.notify_admin_failure", notify_admin_failure)

        await scheduler.run_once()
        assert alerts == []
