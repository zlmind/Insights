"""Gate for the publisher extension: vitest suite passes and dist builds.

    python scripts/check_extension_tests.py    EXTENSION_TESTS_OK

Runs `npm test` and `npm run build` inside extension/. A missing node_modules
fails loudly — a gate that silently skipped would read as coverage.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(os.path.dirname(HERE), "extension")


def _run(args):
    proc = subprocess.run(args, cwd=EXT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          shell=(os.name == "nt"))
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main():
    if not os.path.isdir(os.path.join(EXT, "node_modules")):
        print("extension/node_modules missing — run: cd extension && npm install",
              file=sys.stderr)
        return 1
    code, out = _run(["npm", "test"])
    if code != 0:
        print(out, file=sys.stderr)
        return 1
    code, out = _run(["npm", "run", "build"])
    if code != 0:
        print(out, file=sys.stderr)
        return 1
    for artifact in ("dist/manifest.json", "dist/service-worker.js",
                     "dist/content/bridge.js", "dist/lib/protocol.js",
                     "dist/popup/popup.html", "dist/options/options.html"):
        if not os.path.isfile(os.path.join(EXT, *artifact.split("/"))):
            print(f"build did not produce {artifact}", file=sys.stderr)
            return 1
    print("EXTENSION_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
