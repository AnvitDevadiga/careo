"""Scriptable test program. argv[1] is the role ("ref" or "cand").

stdin is a JSON object; the behavior for this role is taken from
input[role] if present, else input["both"], else {} (echo input["value"]).
Behavior keys: value, raw (bytes to stdout, as str), stderr, exit, sleep,
big (bytes of output), grandchild (pidfile path), random, signal.
"""

import json
import os
import signal
import subprocess
import sys
import time
import uuid

role = sys.argv[1] if len(sys.argv) > 1 else "ref"
req = json.loads(sys.stdin.read())
spec = req.get(role, req.get("both", {}))

if "grandchild" in spec:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(spec["grandchild"], "w") as fh:
        fh.write(str(child.pid))
if "stderr" in spec:
    sys.stderr.write(spec["stderr"])
    sys.stderr.flush()
if "sleep" in spec:
    time.sleep(spec["sleep"])
if "signal" in spec:
    os.kill(os.getpid(), getattr(signal, spec["signal"]))
if "big" in spec:
    sys.stdout.write("x" * spec["big"])
elif "raw" in spec:
    sys.stdout.write(spec["raw"])
elif spec.get("random"):
    print(json.dumps({"id": str(uuid.uuid4())}))
else:
    print(json.dumps(spec.get("value", req.get("value")), ensure_ascii=False))
sys.stdout.flush()
sys.exit(spec.get("exit", 0))
