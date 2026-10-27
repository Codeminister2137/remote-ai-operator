# Sample task: issue CSV import

Build CSV import for a small issue tracker. Input columns are id, title, status.
Trim values. id and title must be nonempty; status is open or closed, case-insensitive.
Reject a file with missing, duplicate or extra headers. Return accepted issues and
row-level errors. Report physical CSV record-ending line numbers, with the header at line 1. Skip blank rows. Reject duplicate
IDs against existing issues and earlier accepted rows. Invalid rows do not reserve
IDs. Preserve input issues and deterministic row order. The application UI shows accepted rows and errors by executing approved source in the isolated runner.

The seed module deliberately raises NotImplementedError. Protected tests specify
behaviour independently. Scripted mode installs an explicitly labelled reference
candidate; it is not evidence that an AI implemented this feature.

Public API: `import_issues(text, existing=())`, with existing a sequence of issue
objects. Return a dictionary {"issues": [...], "errors": [...]}, never a tuple.
Accepted issues have string id, title, status. Each error has integer row and string
message. Empty input and invalid headers raise ValueError. This contract is supplied
to the model in its instructions; protected tests remain independent and unchanged.