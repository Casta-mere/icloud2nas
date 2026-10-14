import io
import json
import threading

from icloud2nas.engine.events import Emitter


def test_emit_writes_one_json_line_with_ev_and_ts() -> None:
    buf = io.StringIO()
    Emitter(buf).emit("started", mode="auth", pid=42)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["ev"] == "started" and rec["mode"] == "auth" and rec["pid"] == 42
    assert rec["ts"].endswith("+00:00")


def test_emit_keeps_unicode_and_stringifies_odd_values() -> None:
    buf = io.StringIO()
    Emitter(buf).emit("progress", last="照片.HEIC", path=__import__("pathlib").Path("/a"))
    rec = json.loads(buf.getvalue())
    assert rec["last"] == "照片.HEIC" and rec["path"] == "/a"
    assert "照片" in buf.getvalue()  # not \u escaped


def test_emit_is_thread_safe() -> None:
    buf = io.StringIO()
    em = Emitter(buf)
    threads = [threading.Thread(target=lambda i=i: em.emit("n", i=i)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = buf.getvalue().splitlines()
    assert len(lines) == 50
    assert sorted(json.loads(line)["i"] for line in lines) == list(range(50))
