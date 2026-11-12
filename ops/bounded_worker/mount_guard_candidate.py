"""Experimental composition; never invoked by the production guard or launcher.

This does not establish that filtering mountinfo preserves the security policy.
"""
import os

from . import mount_observer_prototype as observer
from . import storage_guard


def evaluate(expected):
    """Run existing checks on the experimental view, without executing a child.

    Observer descriptors must be closed before this returns. No result of this
    function is an admission decision or evidence of worker isolation.
    """
    storage_guard.no_extra_descriptors()
    mounts = observer.observe()
    storage_guard.no_extra_descriptors()
    storage_guard.verify(expected, mounts, os.stat, os.statvfs)
    fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = observer._read_bounded('/proc/thread-self/fdinfo/' + str(fd), observer.MAX_FDINFO)
        storage_guard.verify_cwd(expected, mounts, os.getcwd(), info.decode('ascii'))
    finally:
        os.close(fd)
