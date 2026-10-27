"""Run the applied sample feature inside the isolated execution environment."""

import json
import sys

sys.path.insert(0, "/workspace")
from issue_import import import_issues

request = json.loads(sys.stdin.read(65536))
result = import_issues(request["csv_text"], request["existing"])
print("OPERATOR_PREVIEW " + json.dumps(result), flush=True)
