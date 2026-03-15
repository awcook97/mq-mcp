#!/usr/bin/env python3
"""Generate Python protobuf stubs from the proto/ directory.

Requires grpcio-tools:
    pip install grpcio-tools

Run from the repo root:
    python scripts/gen_proto.py
"""

import subprocess
import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
proto_dir = repo_root / "proto"

result = subprocess.run(
    [
        sys.executable, "-m", "grpc_tools.protoc",
        f"--proto_path={proto_dir}",
        f"--python_out={proto_dir}",
        str(proto_dir / "Routing.proto"),
        str(proto_dir / "Network.proto"),
    ],
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    print("protoc failed:")
    print(result.stderr)
    sys.exit(1)

print("Generated:")
for f in sorted(proto_dir.glob("*_pb2.py")):
    print(f"  {f.name}")
