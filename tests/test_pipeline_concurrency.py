from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_planning_and_execution_have_independent_slot_pools() -> None:
    from backend.services import pipeline_runner

    assert pipeline_runner._planning_slots is not pipeline_runner._execution_slots


def test_fourth_phase_waits_for_a_released_slot(monkeypatch) -> None:
    from agents.pipeline_agent import run_states as states
    from backend.services import pipeline_runner

    async def scenario() -> None:
        slots = asyncio.Semaphore(3)
        release = asyncio.Event()
        started: list[str] = []
        statuses: list[tuple[str, str]] = []

        monkeypatch.setattr(
            pipeline_runner,
            "update_pipeline_run",
            lambda run_id, **kwargs: statuses.append((run_id, kwargs["status"])),
        )
        monkeypatch.setattr(
            pipeline_runner,
            "get_pipeline_run",
            lambda _run_id: SimpleNamespace(status=states.QUEUED),
        )

        async def phase(run_id: str) -> None:
            started.append(run_id)
            await release.wait()

        tasks = [
            asyncio.create_task(
                pipeline_runner._run_with_slot(run_id, states.PLANNING, phase(run_id), slots)
            )
            for run_id in ("run-1", "run-2", "run-3", "run-4")
        ]
        await asyncio.sleep(0)
        assert started == ["run-1", "run-2", "run-3"]
        assert ("run-4", states.QUEUED) in statuses

        release.set()
        await asyncio.gather(*tasks)
        assert started == ["run-1", "run-2", "run-3", "run-4"]

    asyncio.run(scenario())