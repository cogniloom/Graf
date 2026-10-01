"""Bounded disposable PDF rendering for source review, never remote content."""

import resource
import subprocess
import sys


def main():
    resource.setrlimit(resource.RLIMIT_AS, (1_000_000_000, 1_000_000_000))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32_000_000, 32_000_000))
    source, page, target = sys.argv[1:]
    result = subprocess.run(
        ["pdftoppm", "-f", page, "-l", page, "-scale-to", "1800", "-singlefile", "-png", source, target],
        capture_output=True,
        timeout=35,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
