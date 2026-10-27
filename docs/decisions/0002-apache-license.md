# 0002 - Apache-2.0 for the standalone public project

Status: accepted 2026-10-27.

## Owner decision and rationale

The owner selected Apache-2.0 for Remote AI Operator. Their stated reason was that
substantial rights are already granted to event organizers and Apache-2.0 is
acceptable for this standalone project. The event agreement and repository license
remain separate grants; this decision does not rewrite the event terms.

## Alternatives considered

MIT would be shorter and permissive but lacks an explicit patent-license clause.
Apache-2.0 permits broad commercial reuse with explicit contributor patent terms
and more redistribution/notice requirements. Leaving the code without an open-source
license would reserve more rights but not grant standard broad reuse permissions.
The owner chose Apache-2.0 after these options were explained.

## Boundary and implementation

License this newly developed standalone repository's original source/documentation.
Private Workbench code, history, plans and separately maintained products remain
outside the distribution. Third-party dependencies keep their respective licenses.
No publication, account change or private-code release is authorized by this choice.

Keep the canonical Apache license in LICENSE and project attribution in NOTICE.
Declare the SPDX expression and included license files in package metadata.
Use project-contributor attribution without adding personal contact details.
Setuptools 77 is the minimum build backend supporting this metadata format; runtime
dependencies and application behaviour are unchanged. Verify built license payloads.

Sources checked 2026-10-27:
https://www.apache.org/licenses/LICENSE-2.0
https://www.apache.org/foundation/license-faq.html
https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html
