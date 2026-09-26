"""Start the installed local MCP bridge without embedding machine-specific paths."""

import json
import os
import sys
from pathlib import Path

if os.environ.get("GRAF_HOME"):
    config = Path(os.environ["GRAF_HOME"]).expanduser().resolve() / "app.json"
    installed = json.loads(config.read_text())
    pointer = {"config": str(config), "python": installed["python"], "bridge": installed["mcp_bridge"]}
else:
    path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "graf/runtime.json"
    if not path.is_file():
        sys.exit("Install Graf and run ./graf plugin-install first.")
    pointer = json.loads(path.read_text())
os.execv(pointer["python"], [pointer["python"], pointer["bridge"], pointer["config"]])
