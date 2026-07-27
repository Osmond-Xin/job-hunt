"""Story-bank writes under evaluate-batch concurrency."""

from __future__ import annotations

def test_concurrent_story_bank_writes_keep_every_entry(tmp_path, monkeypatch) -> None:
    """Three jobs finishing together must not overwrite each other's stories.

    Note this passes with or without `_STORY_BANK_LOCK` today: the read-modify-
    write is synchronous end to end, so asyncio cannot interleave it. The test
    pins the observable invariant rather than the mechanism — it starts failing
    if someone adds an `await` inside the critical section and drops the lock.
    """
    import asyncio

    from job_hunt.nodes import personalize as personalize_module
    from job_hunt.models.job import JobMeta

    bank = tmp_path / "story-bank.md"
    monkeypatch.setattr(personalize_module, "_STORY_BANK_PATH", bank)

    real_read = personalize_module._STORY_BANK_PATH.read_text

    async def run() -> None:
        async def one(index: int) -> None:
            state = {
                "evaluation_blocks": {"interview_prep": f"story {index}"},
                "jd_meta": JobMeta(company=f"Co{index}", title="Engineer"),
            }
            # Yield control mid-node so the interleaving is real, not lucky.
            await asyncio.sleep(0)
            await personalize_module.update_story_bank(state, None)

        await asyncio.gather(*(one(index) for index in range(3)))

    asyncio.run(run())
    written = bank.read_text(encoding="utf-8")
    for index in range(3):
        assert f"story {index}" in written, written
