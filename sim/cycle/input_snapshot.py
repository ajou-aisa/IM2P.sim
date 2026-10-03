from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO, Final

COPY_RESERVE_BYTES: Final = 256 * 1024 * 1024
# ponytail: 8x input budgets SQLite rows, indexes, journal, and temp files; staged writes still fail closed if growth exceeds it.
SQLITE_WORKING_SET_FACTOR: Final = 8
UNSUPPORTED_CLONE: Final = frozenset((errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EXDEV))


def _digest(stream: BinaryIO) -> bytes:
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(block)
    return value.digest()


def _clone_fd(descriptor: int, destination: Path) -> bool:
    if sys.platform != 'darwin':
        return False
    library = ctypes.CDLL(None, use_errno=True)
    try:
        clone = library.fclonefileat
    except AttributeError:
        return False
    clone.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
    clone.restype = ctypes.c_int
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if clone(descriptor, directory, os.fsencode(destination.name), 0) == 0:
            return True
        error = ctypes.get_errno()
        if error in UNSUPPORTED_CLONE:
            return False
        raise OSError(error, os.strerror(error), str(destination))
    finally:
        os.close(directory)


def require_storage_budget(destination: Path, required: int, purpose: str = 'storage budget') -> None:
    if shutil.disk_usage(destination.parent).free < required:
        raise OSError(errno.ENOSPC, f'{purpose} requires {required} free bytes', str(destination))


def snapshot_file(descriptor: int, destination: Path) -> str:
    source = os.fstat(descriptor)
    if not stat.S_ISREG(source.st_mode):
        raise OSError(errno.EINVAL, 'snapshot source must be a regular file')
    if os.path.lexists(destination):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
    require_storage_budget(destination, source.st_size + COPY_RESERVE_BYTES, 'snapshot copy budget')
    with tempfile.TemporaryDirectory(prefix='input-snapshot-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'snapshot'
        if _clone_fd(descriptor, staged):
            method = 'DARWIN_FCLONEFILEAT'
        else:
            with os.fdopen(descriptor, 'rb', closefd=False) as reader, staged.open('xb') as writer:
                reader.seek(0)
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            method = 'EXCLUSIVE_COPY'
        copied = staged.stat()
        if (source.st_dev, source.st_ino) == (copied.st_dev, copied.st_ino):
            raise OSError(errno.EINVAL, 'snapshot must not be a hard link')
        with os.fdopen(descriptor, 'rb', closefd=False) as reader, staged.open('rb') as captured:
            reader.seek(0)
            original_hash = _digest(reader)
            snapshot_hash = _digest(captured)
        if original_hash != snapshot_hash or source.st_size != copied.st_size:
            raise OSError(errno.EIO, 'snapshot byte identity mismatch')
        os.link(staged, destination)
    return method
