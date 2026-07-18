#!/usr/bin/env python3
"""Package the agent/ source tree into agent.tar.gz for inline deployment.

The Terraform agent-engine module embeds this archive via filebase64() at plan
time, so it MUST exist before `terraform plan`/`apply` (CI runs this first).

Builds a byte-for-byte reproducible archive (sorted entries, fixed mtimes,
zeroed ownership, no gzip header timestamp) using only the Python standard
library — no GNU tar dependency, so the same bytes come out on macOS, Linux,
and CI alike. A non-reproducible archive here causes a persistent, harmless
but noisy Terraform diff on every future plan (the reasoning engine's
source_code_spec never converges), so determinism is not cosmetic.
"""
import gzip
import io
import os
import tarfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENT_DIR = os.path.join(REPO_ROOT, "agent")
OUT_PATH = os.path.join(REPO_ROOT, "agent.tar.gz")

EXCLUDE_NAMES = {"__pycache__", ".env", ".env.example"}
EXCLUDE_SUFFIXES = (".pyc",)

FIXED_MTIME = 0  # 1970-01-01 — both the per-entry tar mtime and the gzip header mtime


def is_excluded(name):
    return name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIXES)


def collect_files():
    paths = []
    for dirpath, dirnames, filenames in os.walk(AGENT_DIR):
        dirnames[:] = sorted(d for d in dirnames if not is_excluded(d))
        for filename in sorted(filenames):
            if is_excluded(filename):
                continue
            paths.append(os.path.join(dirpath, filename))
    return sorted(paths)


def build_tarinfo(path, arcname, size):
    info = tarfile.TarInfo(name=arcname)
    info.size = size
    info.mtime = FIXED_MTIME
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = os.stat(path).st_mode & 0o777
    return info


def main():
    files = collect_files()
    if not files:
        raise SystemExit(f"No files found under {AGENT_DIR} — nothing to package")

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        for path in files:
            arcname = "agent/" + os.path.relpath(path, AGENT_DIR)
            with open(path, "rb") as f:
                data = f.read()
            info = build_tarinfo(path, arcname, len(data))
            tar.addfile(info, io.BytesIO(data))

    with open(OUT_PATH, "wb") as out:
        # mtime=0 suppresses gzip's own header timestamp — the piece plain
        # `tar -z` (even GNU tar) does not reliably suppress on its own.
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=FIXED_MTIME) as gz:
            gz.write(tar_buffer.getvalue())

    print(f"Built {OUT_PATH} (reproducible, {len(files)} files) from agent/")


if __name__ == "__main__":
    main()
