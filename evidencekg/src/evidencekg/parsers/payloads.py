"""Private binary parser transport: JSON carries metadata, never archive payloads."""

import os
import re
import stat
import uuid


def write_payload(directory, content):
    # Names never come from archive members or other untrusted source metadata.
    name = "payload-" + uuid.uuid4().hex
    with (directory / name).open("xb") as output:
        output.write(content)
    return {"data_file": name}


def read_payload(directory, name, limit):
    if not isinstance(name, str) or not re.fullmatch(r"payload-[0-9a-f]{32}", name):
        raise ValueError("Invalid parser payload name")
    fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Invalid parser payload type or size")
        content = source.read(info.st_size + 1)
        if len(content) != info.st_size:
            raise ValueError("Parser payload changed while reading")
    return content
