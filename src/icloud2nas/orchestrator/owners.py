"""Per-account file owners (spec §16.3): host users, defaults, chown of what we create.

The orchestrator runs as root in the container and spawns each worker as its account's NAS
user. When it does not run as root (`PUID`/`PGID` set: single-user mode) owners are ignored:
every worker runs as the orchestrator's own user and nothing is chowned.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from icloud2nas.engine.trash import (
    STORE_DIRNAME,
    TRASH_DIRNAME,
    open_dir,
    store_dir,
    subdir,
    trash_dir,
)
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.store.models import Account

log = logging.getLogger(__name__)

Owner = tuple[int, int]
UID_MIN, UID_MAX = 1000, 59999
_warned: set[Path] = set()  # paths whose chown already failed once (logged once each)


@dataclass(frozen=True)
class HostUser:
    name: str
    uid: int
    gid: int
    group: str  # name of the primary group, or the gid as text when /etc/group lacks it
    home: str


def _num(text: str) -> int | None:
    return int(text) if text.isascii() and text.isdigit() else None


def _read_lines(path: Path) -> list[list[str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.split(":") for line in text.splitlines() if line and not line.startswith("#")]


def read_host_users(host_etc: Path) -> list[HostUser]:
    """Human users of the host: uid 1000-59999 with a home under /home; [] when unreadable."""
    groups: dict[int, str] = {}
    for fields in _read_lines(host_etc / "group"):
        gid = _num(fields[2]) if len(fields) >= 3 else None
        if gid is not None:
            groups.setdefault(gid, fields[0])
    users: list[HostUser] = []
    for fields in _read_lines(host_etc / "passwd"):
        if len(fields) < 7:
            continue
        uid, gid = _num(fields[2]), _num(fields[3])
        if uid is None or gid is None:
            continue
        name, home = fields[0], fields[5]
        if not UID_MIN <= uid <= UID_MAX or not home.startswith("/home/"):
            continue
        users.append(HostUser(name, uid, gid, groups.get(gid, str(gid)), home))
    return users


def owner_names(host_etc: Path) -> dict[int, str]:
    return {u.uid: u.name for u in read_host_users(host_etc)}


def default_owner(path: Path, root: Path | None = None) -> Owner:
    """uid, gid of the nearest existing ancestor of path (path itself included), never above root.

    ValueError when path is not inside root; FileNotFoundError when not even root exists.
    """
    here = Path(path)
    if root is not None and not here.is_relative_to(root):
        raise ValueError(f"{str(path)!r} is outside {str(root)!r}")
    while True:
        try:
            st = os.stat(here)
        except FileNotFoundError:
            if here == root or here.parent == here:
                raise
            here = here.parent
            continue
        return st.st_uid, st.st_gid


def privileged() -> bool:
    return os.geteuid() == 0


def owner_for(account: Account) -> Owner | None:
    """The uid:gid the account's workers run as; None in single-user mode or when unset."""
    if not privileged() or account.owner_uid is None or account.owner_gid is None:
        return None
    return account.owner_uid, account.owner_gid


class LibraryMissing(Exception):
    """A synced library's folder vanished before its worker started (spec §16.2 dir_missing)."""


def _warn_once(path: Path, what: str, exc: OSError) -> None:
    if path not in _warned:
        _warned.add(path)
        log.warning("could not %s %s: %s", what, path, exc.strerror or exc)


def chown_path(path: Path, owner: Owner | None) -> None:
    """chown one path (never following a symlink) when privileged and it differs; never raises."""
    if owner is None or not privileged():
        return
    try:
        st = os.lstat(path)
        if (st.st_uid, st.st_gid) == owner:
            return
        os.chown(path, owner[0], owner[1], follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:  # e.g. EPERM on a filesystem that refuses chown
        _warn_once(path, f"chown to {owner[0]}:{owner[1]}", exc)


def _chown_at(dir_fd: int, name: str, shown: Path, owner: Owner) -> None:
    """chown_path for an entry of an open directory: the name is resolved relative to dir_fd."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (st.st_uid, st.st_gid) == owner:
            return
        if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            return  # a hardlink may be planted to a file outside the tree: never chown it
        os.chown(name, owner[0], owner[1], dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        _warn_once(shown, f"chown to {owner[0]}:{owner[1]}", exc)


def ensure_owned_dir(path: Path, owner: Owner | None) -> None:
    """mkdir -p path; every directory this call created is chowned to owner (when privileged)."""
    created: list[Path] = []
    here = Path(path)
    while not here.exists() and not here.is_symlink() and here.parent != here:
        created.append(here)
        here = here.parent
    path.mkdir(parents=True, exist_ok=True)
    for directory in reversed(created):
        chown_path(directory, owner)


def chown_tree(path: Path, owner: Owner | None) -> None:
    """chown a directory and everything below it (symlinks themselves, never their targets).

    The walk holds each directory open (os.fwalk without following links) and chowns entries
    relative to it, so a parent swapped for a symlink mid-walk cannot redirect it. Hardlinked
    files are left alone.
    """
    if owner is None or not privileged() or path.is_symlink() or not path.is_dir():
        return
    chown_path(path, owner)
    for dirpath, dirnames, filenames, dir_fd in os.fwalk(path, follow_symlinks=False):
        here = Path(dirpath)
        for name in (*dirnames, *filenames):
            _chown_at(dir_fd, name, here / name, owner)


def _chmod(path: Path, mode: int, *, directory: bool) -> None:
    """chmod without following a symlink: open with O_NOFOLLOW, then fchmod (lchmod is missing
    on Linux); a path that turned into a symlink is skipped."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_DIRECTORY if directory else 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno != errno.ELOOP:
            _warn_once(path, f"chmod to {mode:o}", exc)
        return
    try:
        os.fchmod(fd, mode)
    except OSError as exc:
        _warn_once(path, f"chmod to {mode:o}", exc)
    finally:
        os.close(fd)


def secure_account_dirs(config: Config, account_id: str) -> None:
    """The account's cookies, sessions and plans are its user's alone: dirs 0700, files 0600."""
    account_dir = config.accounts_dir / account_id
    for directory in (account_dir, config.account_plans_dir(account_id)):
        if directory.is_symlink() or not directory.is_dir():
            continue
        _chmod(directory, 0o700, directory=True)
        for dirpath, dirnames, filenames in os.walk(directory):
            for name in (*dirnames, *filenames):
                entry = Path(dirpath) / name
                try:
                    st = os.lstat(entry)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    wanted = 0o700
                elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                    wanted = 0o600
                else:
                    continue  # symlinks and hardlinks: leave them (and their targets) alone
                if stat.S_IMODE(st.st_mode) != wanted:
                    _chmod(entry, wanted, directory=stat.S_ISDIR(st.st_mode))


def prepare_spawn(
    config: Config,
    account_id: str,
    owner: Owner | None,
    library_dir: Path | None = None,
    *,
    require_library: bool = False,
) -> None:
    """Before a worker starts: the dirs it writes to exist and belong to the user it runs as.

    The account dir (cookies) and the account's plans dir are claimed whole; for a library run
    the chain up to `<library>/.icloud2nas/trash` is created as the owner, and an existing store
    (say, made by root during the legacy migration) is handed over. Day folders are left to the
    worker. Without an owner only the account's own dirs are ensured. Either way the account
    dir and plans dir are made private (0700 dirs, 0600 files).

    With require_library (a library whose full sync has completed) the library folder must
    already exist: only `.icloud2nas/trash` is created beneath it, never the folder itself, and
    LibraryMissing is raised when it is gone (spec §16.2: no re-download into a new folder).
    """
    if library_dir is not None and require_library and not library_dir.is_dir():
        raise LibraryMissing(str(library_dir))
    ensure_owned_dir(config.cookie_dir(account_id), owner)
    ensure_owned_dir(config.account_plans_dir(account_id), owner)
    secure_account_dirs(config, account_id)
    if owner is None:
        return
    chown_tree(config.accounts_dir / account_id, owner)
    chown_tree(config.account_plans_dir(account_id), owner)
    if library_dir is not None:
        prepare_store(config.photos_dir, library_dir, owner, require_library=require_library)


def chown_fd(fd: int, shown: Path, owner: Owner) -> None:
    """fchown an open file or directory when it differs; never raises (failures logged once)."""
    try:
        st = os.fstat(fd)
        if (st.st_uid, st.st_gid) == owner:
            return
        os.fchown(fd, owner[0], owner[1])
    except OSError as exc:
        _warn_once(shown, f"chown to {owner[0]}:{owner[1]}", exc)


def prepare_store(
    photos: Path, library_dir: Path, owner: Owner, *, require_library: bool = False
) -> None:
    """Make `<library>/.icloud2nas/trash` for the owner without ever following a symlink.

    The library folder is reached from the photos root one O_NOFOLLOW component at a time and
    the store is made relative to those fds, so a symlinked parent, library, `.icloud2nas` or
    `trash` fails with ValueError instead of being chowned. Folders made on the way are
    fchowned to owner; the store and trash are handed over even when they exist (say, made by
    root during the legacy migration). With require_library the library folder itself is
    never made: LibraryMissing when it is gone.
    """

    def made(fd: int, shown: Path) -> None:
        chown_fd(fd, shown, owner)

    try:
        cur = open_dir(photos, library_dir, create=not require_library, on_create=made)
    except FileNotFoundError:
        raise LibraryMissing(str(library_dir)) from None
    try:
        for name, path in ((STORE_DIRNAME, store_dir(library_dir)),
                           (TRASH_DIRNAME, trash_dir(library_dir))):
            try:
                nxt = subdir(cur, name, path, create=True, on_create=made)
            except FileNotFoundError:  # the library vanished meanwhile
                raise LibraryMissing(str(library_dir)) from None
            os.close(cur)
            cur = nxt
            chown_fd(cur, path, owner)
    finally:
        os.close(cur)
