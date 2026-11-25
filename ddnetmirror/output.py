"""File-safety helpers, the user-facing error type and display escaping, all standard-library only."""

import contextlib
import os
import stat
import tempfile


class FetchError(Exception):
    """A failure reported to the user as a one-line message."""


def write_new_file(path, text, force=False):
    """Write text as UTF-8 without any changes to a new owner-only file.

    Refuses to overwrite. If writing or closing fails, removes the file it created.
    With force, an existing regular file is replaced instead: the text goes to a
    new owner-only temporary file in the same directory, which is then renamed
    over path, so the previous file stays intact unless the rename succeeds.
    """
    data = text.encode("utf-8")
    if force:
        check_replaceable(path)
        # fixed short name: a long destination name must not overflow NAME_MAX
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".ddn-", suffix=".tmp")
        write_fd(fd, data, temp, sync=True)
        try:
            os.replace(temp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FetchError(f"{path} already exists; not overwriting") from None
    write_fd(fd, data, path)


def write_fd(fd, data, path, sync=False):
    """Write data to the new file fd and close it; on failure remove path."""
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if sync:
                f.flush()
                os.fsync(f.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise


def check_replaceable(path):
    """Allow only a missing path or an existing regular file (never a symlink)."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode):
        kind = "a symbolic link"
    elif not stat.S_ISREG(mode):
        kind = "not a regular file"
    else:
        return
    raise FetchError(f"{path} is {kind}; not replacing it with --force "
                     "(remove it, or use a new --output-dir)")


def ensure_absent(path, force=False):
    if force:
        check_replaceable(path)
    elif os.path.exists(path):
        raise FetchError(f"{path} already exists; not overwriting")


def shown(value):
    """Escape control characters (C0, DEL, C1) in an LLDP value for display."""
    return "".join(
        f"\\x{ord(c):02x}" if ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F else c
        for c in str(value)
    )
