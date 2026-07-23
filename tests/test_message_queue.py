"""Free-form chat queue (Grok-style FIFO while busy)."""

from __future__ import annotations


def test_queue_fifo_order_and_cap() -> None:
    """Pure queue semantics used by ShellScreen._msg_queue."""
    max_q = 20
    q: list[dict] = []

    def enqueue(text: str) -> bool:
        if len(q) >= max_q:
            return False
        q.append({"text": text, "skill_name": None, "painted": True})
        return True

    def pump() -> str | None:
        if not q:
            return None
        return str(q.pop(0)["text"])

    assert enqueue("a")
    assert enqueue("b")
    assert enqueue("c")
    assert [x["text"] for x in q] == ["a", "b", "c"]
    assert pump() == "a"
    assert pump() == "b"
    assert [x["text"] for x in q] == ["c"]

    # 1 left ("c"); fill to max_q then overflow
    for i in range(max_q - 1):
        assert enqueue(f"m{i}")
    assert len(q) == max_q
    assert not enqueue("overflow")
    assert len(q) == max_q


def test_busy_means_agent_or_doc_running() -> None:
    """_is_send_busy policy: either agent turn or orch.running."""

    def is_busy(chat_busy: bool, orch_running: bool) -> bool:
        return bool(chat_busy or orch_running)

    assert is_busy(True, False)
    assert is_busy(False, True)
    assert is_busy(True, True)
    assert not is_busy(False, False)
