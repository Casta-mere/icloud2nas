from __future__ import annotations

import os
from pathlib import Path

import pytest

from icloud2nas.orchestrator import owners
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.owners import (
    HostUser,
    default_owner,
    ensure_owned_dir,
    owner_for,
    prepare_spawn,
    read_host_users,
)
from icloud2nas.orchestrator.store.models import Account

PASSWD = """\
root:x:0:0:root:/root:/bin/bash
daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin
Castamere:x:1000:10::/home/Castamere:/bin/sh
Today_red:x:1001:10::/home/Today_red:/bin/sh
WY:x:1003:100::/home/WY:/bin/sh
thunder:x:1004:100::/var/lib/xunlei:/bin/false
earlyoom:x:61876:61876::/home/earlyoom:/usr/sbin/nologin
broken line without colons
"""
GROUP = """\
root:x:0:
admin:x:10:Castamere,Today_red
"""


def _host_etc(tmp_path: Path) -> Path:
    etc = tmp_path / "host-etc"
    etc.mkdir()
    (etc / "passwd").write_text(PASSWD, encoding="utf-8")
    (etc / "group").write_text(GROUP, encoding="utf-8")
    return etc


class Chowns:
    """Records chown calls instead of performing them (tests do not need root)."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, int, int]] = []

    def __call__(
        self, path: str | Path, uid: int, gid: int, *, dir_fd: int | None = None, **_: object
    ) -> None:
        if dir_fd is not None:  # an fd-relative chown (chown_tree): name the full path
            path = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / path
        self.calls.append((Path(path), uid, gid))

    def fchown(self, fd: int, uid: int, gid: int) -> None:
        self.calls.append((Path(os.readlink(f"/proc/self/fd/{fd}")), uid, gid))


@pytest.fixture
def chowns(monkeypatch: pytest.MonkeyPatch) -> Chowns:
    recorder = Chowns()
    monkeypatch.setattr(owners.os, "chown", recorder)
    monkeypatch.setattr(owners.os, "fchown", recorder.fchown)
    monkeypatch.setattr(owners, "privileged", lambda: True)
    owners._warned.clear()
    return recorder


def test_read_host_users_filters_system_accounts(tmp_path: Path) -> None:
    users = read_host_users(_host_etc(tmp_path))
    assert users == [
        HostUser("Castamere", 1000, 10, "admin", "/home/Castamere"),
        HostUser("Today_red", 1001, 10, "admin", "/home/Today_red"),
        HostUser("WY", 1003, 100, "100", "/home/WY"),
    ]


def test_read_host_users_missing_files_is_empty(tmp_path: Path) -> None:
    assert read_host_users(tmp_path / "nope") == []
    etc = tmp_path / "only-passwd"
    etc.mkdir()
    (etc / "passwd").write_text(PASSWD, encoding="utf-8")
    assert [u.group for u in read_host_users(etc)] == ["10", "10", "100"]  # no group file


def test_default_owner_walks_up_to_existing_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = tmp_path / "photos"
    (photos / "Today_red").mkdir(parents=True)
    owners_by_path = {photos: (0, 0), photos / "Today_red": (1001, 10)}
    real_stat = os.stat

    def fake_stat(path: str | Path, *args: object, **kwargs: object) -> os.stat_result:
        st = real_stat(path)
        uid, gid = owners_by_path.get(Path(path), (st.st_uid, st.st_gid))
        fields = list(st)
        fields[4], fields[5] = uid, gid
        return os.stat_result(fields)

    monkeypatch.setattr(owners.os, "stat", fake_stat)
    assert default_owner(photos / "Today_red" / "Photos" / "iCloud", photos) == (1001, 10)
    assert default_owner(photos / "nobody" / "x", photos) == (0, 0)
    assert default_owner(photos, photos) == (0, 0)


def test_default_owner_never_reads_above_the_root(tmp_path: Path) -> None:
    root = tmp_path / "missing-root"
    with pytest.raises(FileNotFoundError):
        default_owner(root / "a" / "b", root)
    with pytest.raises(ValueError):
        default_owner(tmp_path / "elsewhere", root)


def test_owner_for_needs_privilege_and_both_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    account = Account(owner_uid=1000, owner_gid=10)
    monkeypatch.setattr(owners, "privileged", lambda: True)
    assert owner_for(account) == (1000, 10)
    assert owner_for(Account(owner_uid=None, owner_gid=None)) is None
    monkeypatch.setattr(owners, "privileged", lambda: False)
    assert owner_for(account) is None


def test_ensure_owned_dir_chowns_only_created_dirs(tmp_path: Path, chowns: Chowns) -> None:
    (tmp_path / "a").mkdir()
    ensure_owned_dir(tmp_path / "a" / "b" / "c", (1000, 10))
    assert (tmp_path / "a" / "b" / "c").is_dir()
    b = tmp_path / "a" / "b"
    assert chowns.calls == [(b, 1000, 10), (b / "c", 1000, 10)]
    chowns.calls.clear()
    ensure_owned_dir(tmp_path / "a" / "b" / "c", (1000, 10))
    assert chowns.calls == []


def test_ensure_owned_dir_without_owner_or_privilege_only_mkdirs(
    tmp_path: Path, chowns: Chowns, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_owned_dir(tmp_path / "x" / "y", None)
    monkeypatch.setattr(owners, "privileged", lambda: False)
    ensure_owned_dir(tmp_path / "p" / "q", (1000, 10))
    assert (tmp_path / "x" / "y").is_dir() and (tmp_path / "p" / "q").is_dir()
    assert chowns.calls == []


def test_chown_failure_is_logged_once_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(owners.os, "chown", refuse)
    monkeypatch.setattr(owners, "privileged", lambda: True)
    owners._warned.clear()
    ensure_owned_dir(tmp_path / "d", (1000, 10))
    owners.chown_path(tmp_path / "d", (1000, 10))
    assert (tmp_path / "d").is_dir()
    assert sum("could not chown" in r.message for r in caplog.records) == 1


def test_cookie_dir_chowned_before_spawn(
    config: Config, tmp_path: Path, chowns: Chowns
) -> None:
    config.cookie_dir("acc1").mkdir(parents=True)  # created by root at account creation
    (config.cookie_dir("acc1") / "a@icloud.com").write_text("cookie", encoding="utf-8")
    library = config.photos_dir / "shared" / "家庭"
    prepare_spawn(config, "acc1", (1000, 10), library)
    touched = {path for path, _, _ in chowns.calls}
    account_dir = config.accounts_dir / "acc1"
    assert {
        account_dir,
        config.cookie_dir("acc1"),
        config.cookie_dir("acc1") / "a@icloud.com",
        config.account_plans_dir("acc1"),
        config.photos_dir / "shared",
        library,
        library / ".icloud2nas",
        library / ".icloud2nas" / "trash",
    } <= touched
    assert config.photos_dir not in touched and config.accounts_dir not in touched
    assert all((uid, gid) == (1000, 10) for _, uid, gid in chowns.calls)
    assert not any(p.parent.name == "trash" for p in touched)  # no empty day dir is made


def test_single_user_mode_ignores_owner(
    config: Config, chowns: Chowns, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(owners, "privileged", lambda: False)
    account = Account(id="acc1", owner_uid=1000, owner_gid=10)
    owner = owner_for(account)
    assert owner is None
    prepare_spawn(config, "acc1", owner, config.photos_dir / "lib")
    assert config.cookie_dir("acc1").is_dir()
    assert not (config.photos_dir / "lib").exists()  # the worker creates it as itself
    assert chowns.calls == []


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_prepare_spawn_makes_account_dirs_private(config: Config, chowns: Chowns) -> None:
    cookies = config.cookie_dir("acc1")
    cookies.mkdir(parents=True)
    os.chmod(cookies.parent, 0o755)
    os.chmod(cookies, 0o755)
    (cookies / "session").write_text("trusted", encoding="utf-8")
    os.chmod(cookies / "session", 0o644)
    (cookies / "sub").mkdir()
    prepare_spawn(config, "acc1", (1000, 10))
    for directory in (config.accounts_dir / "acc1", cookies, cookies / "sub",
                      config.account_plans_dir("acc1")):
        assert _mode(directory) == 0o700, directory
    assert _mode(cookies / "session") == 0o600


def test_create_time_and_single_user_dirs_are_private_too(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(owners, "privileged", lambda: False)
    prepare_spawn(config, "acc2", None)
    assert _mode(config.accounts_dir / "acc2") == 0o700
    assert _mode(config.cookie_dir("acc2")) == 0o700
    assert _mode(config.account_plans_dir("acc2")) == 0o700


def test_chown_tree_skips_hardlinks_and_owned_entries(
    config: Config, tmp_path: Path, chowns: Chowns
) -> None:
    account_dir = config.accounts_dir / "acc1"
    account_dir.mkdir(parents=True)
    precious = tmp_path / "shadow"
    precious.write_text("root only", encoding="utf-8")
    os.link(precious, account_dir / "planted")  # a hardlink to a file outside the tree
    (account_dir / "cookie").write_text("c", encoding="utf-8")
    me = os.lstat(account_dir / "cookie")
    owners.chown_tree(account_dir, (1000, 10))
    touched = {p for p, _, _ in chowns.calls}
    assert account_dir / "planted" not in touched
    assert account_dir / "cookie" in touched
    chowns.calls.clear()
    owners.chown_tree(account_dir, (me.st_uid, me.st_gid))  # already owned: nothing to do
    assert chowns.calls == []


def test_read_host_users_ignores_non_ascii_digits(tmp_path: Path) -> None:
    etc = _host_etc(tmp_path)
    with (etc / "passwd").open("a", encoding="utf-8") as f:
        f.write("odd:x:\u0661\u0660\u0660\u0661:10::/home/odd:/bin/sh\n")
    with (etc / "group").open("a", encoding="utf-8") as f:
        f.write("weird:x:\u0661\u0660:\n")
    assert [u.name for u in read_host_users(etc)] == ["Castamere", "Today_red", "WY"]


def test_chmod_never_follows_a_symlink(tmp_path: Path) -> None:
    precious = tmp_path / "shadow"
    precious.write_text("root only", encoding="utf-8")
    os.chmod(precious, 0o640)
    link = tmp_path / "link"
    link.symlink_to(precious)
    owners._chmod(link, 0o600, directory=False)
    owners._chmod(link, 0o700, directory=True)
    assert _mode(precious) == 0o640
    plain = tmp_path / "plain"
    plain.write_text("x", encoding="utf-8")
    owners._chmod(plain, 0o600, directory=False)
    assert _mode(plain) == 0o600


def test_prepare_spawn_never_recreates_a_synced_library(
    config: Config, chowns: Chowns
) -> None:
    library = config.photos_dir / "Castamere" / "Photos" / "iCloud"
    with pytest.raises(owners.LibraryMissing):
        prepare_spawn(config, "acc1", (1000, 10), library, require_library=True)
    with pytest.raises(owners.LibraryMissing):
        prepare_spawn(config, "acc1", None, library, require_library=True)
    assert not (config.photos_dir / "Castamere").exists()
    library.mkdir(parents=True)
    prepare_spawn(config, "acc1", (1000, 10), library, require_library=True)
    assert (library / ".icloud2nas" / "trash").is_dir()
    touched = {p for p, _, _ in chowns.calls}
    assert {library / ".icloud2nas", library / ".icloud2nas" / "trash"} <= touched
    assert library not in touched  # an existing library keeps its owner


def test_prepare_store_never_makes_a_required_library(tmp_path: Path, chowns: Chowns) -> None:
    with pytest.raises(owners.LibraryMissing):
        owners.prepare_store(tmp_path, tmp_path / "gone", (1000, 10), require_library=True)
    assert not (tmp_path / "gone").exists()
    assert chowns.calls == []


def test_prepare_spawn_refuses_a_store_symlinked_to_another_library(
    config: Config, chowns: Chowns
) -> None:
    other = config.photos_dir / "Today_red" / "iCloud" / ".icloud2nas"
    (other / "trash").mkdir(parents=True)
    before = {p: (p.stat().st_uid, p.stat().st_gid) for p in (other, other / "trash")}
    library = config.photos_dir / "Castamere" / "iCloud"
    library.mkdir(parents=True)
    (library / ".icloud2nas").symlink_to(other)
    for required in (True, False):
        with pytest.raises(ValueError, match="symlink"):
            prepare_spawn(config, "acc1", (1000, 10), library, require_library=required)
    touched = {p for p, _, _ in chowns.calls}
    assert not any(p.is_relative_to(other.parent) for p in touched)
    assert {p: (p.stat().st_uid, p.stat().st_gid) for p in before} == before


def test_prepare_spawn_refuses_a_symlinked_parent_above_the_library(
    config: Config, chowns: Chowns
) -> None:
    real = config.photos_dir / "elsewhere"
    (real / "iCloud").mkdir(parents=True)
    (config.photos_dir / "Castamere").symlink_to(real)
    library = config.photos_dir / "Castamere" / "iCloud"
    for required in (True, False):
        with pytest.raises(ValueError, match="symlink"):
            prepare_spawn(config, "acc1", (1000, 10), library, require_library=required)
    assert not (real / "iCloud" / ".icloud2nas").exists()
    assert not any(p.is_relative_to(real) for p, _, _ in chowns.calls)
