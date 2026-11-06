"""Bounded reads of untrusted repository files; not a concurrent-mutation sandbox."""
import os
import stat
from pathlib import Path


def read_bounded(path, limit):
    path = Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('metadata must be a non-symlink regular file')
        # Always bound the actual read, including when the file grows after lstat.
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            actual = os.fstat(stream.fileno())
            if not stat.S_ISREG(actual.st_mode) or getattr(actual, 'st_file_attributes', 0) & 0x400 or (info.st_dev, info.st_ino) != (actual.st_dev, actual.st_ino):
                raise ValueError('metadata must be a non-symlink regular file')
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise ValueError('unable to read metadata') from exc
    if len(raw) > limit:
        raise ValueError('metadata file too large')
    return raw


def toml_data(path, limit):
    import tomllib
    raw = read_bounded(path, limit)
    try:
        return tomllib.loads(raw.decode('utf-8', errors='strict'))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError('invalid UTF-8 TOML metadata') from exc
