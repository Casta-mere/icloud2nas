from typing import Any

from icloud2nas.orchestrator.bus import EventBus


def test_publish_reaches_subscribers_and_unsubscribe_works() -> None:
    bus = EventBus()
    seen: list[tuple[str, dict[str, Any]]] = []
    off = bus.subscribe(lambda topic, payload: seen.append((topic, payload)))
    bus.publish("run.started", run_id="r1")
    off()
    bus.publish("run.done", run_id="r1")
    assert seen == [("run.started", {"run_id": "r1"})]


def test_subscriber_exception_does_not_break_others() -> None:
    bus = EventBus()
    seen: list[str] = []

    def bad(topic: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    bus.subscribe(bad)
    bus.subscribe(lambda topic, payload: seen.append(topic))
    bus.publish("x")
    assert seen == ["x"]
