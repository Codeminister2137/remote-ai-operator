# Runtime model eligibility

Every model called by the judged artifact must be a Meta model. Coding tools and
offline checks are separate from that runtime requirement. Scripted mode calls no
model; Meta mode uses the fixed Meta Responses endpoint and a validated `muse-*`
grant, refusing Contributor models.

Run the offline configuration check from a standalone source checkout or extracted
source distribution (the wheel contains the application, not this source script):

```shell
uv run --locked python scripts/check_submission_eligibility.py --env-file .env --model-mode scripted
uv run --locked python scripts/check_submission_eligibility.py --env-file .env --grant-file grant.json --model-mode meta
```

The check makes no provider calls. It validates the explicit mode and supplied
file, refuses its listed non-Meta credentials, and applies the same grant checks
as the runtime: model, expiry, verified flags, finite allowance and positive
prices. Assignment lines may use an optional \export\ prefix; leading and
trailing whitespace and BOM characters are normalized before the key is compared, so
unusual whitespace cannot disguise a refused credential. Invalid configuration
exits nonzero. Keep actual keys and grants private.

The check reads only the supplied dotenv file as UTF-8, including a UTF-8 BOM if present, and accepts an optional `export` prefix followed by one or more spaces or tabs. It refuses a file containing a non-empty value for any credential on the checker's non-Meta credential list; credential violations report key names without echoing values. This file-only scope is unchanged: the check does not inspect inherited environment variables or the deployed runtime.

An `ELIGIBLE` result applies only to those inputs. It does not inspect inherited
environment variables, image contents, running services, video or other programs.
It does not authenticate a key, verify a credit balance, establish provider billing
protection or authorize inference. A valid grant records facts the owner must
independently verify; it does not prove those facts itself.

Before submission, inspect the actual deployed mode and grant, all runtime model
routes, and the recorded demonstration. Exclude unrelated provider credentials
from the deployed image. Check the description and video against actual effects.
If another application is included, separately verify every model call it makes.
Do not publish keys or grants as evidence.
