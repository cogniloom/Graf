"""Linux parser network denial inherited by OCR subprocesses; fails closed."""

import ctypes
import errno


def deny_network():
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7FFF0000)  # allow other syscalls
    if not context:
        raise RuntimeError("Cannot initialize parser network policy")
    try:
        for name in (b"socket", b"socketpair", b"connect", b"sendto", b"sendmsg", b"sendmmsg"):
            syscall = library.seccomp_syscall_resolve_name(name)
            if syscall >= 0 and library.seccomp_rule_add(context, 0x00050000 | errno.EPERM, syscall, 0):
                raise RuntimeError("Cannot configure parser network policy")
        if library.seccomp_load(context):
            raise RuntimeError("Cannot apply parser network policy")
    finally:
        library.seccomp_release(context)
