"""Fixed host-supplied validation entry point inside the disposable runner."""

import json
import sys
import unittest

sys.path.insert(0, "/workspace")
suite = unittest.defaultTestLoader.discover("/checks", pattern="test_import.py")
result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
print(
    "OPERATOR_CHECKS "
    + json.dumps(
        {
            "tests": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
        }
    ),
    flush=True,
)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun == 7 else 1)
