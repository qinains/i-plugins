# Security

For suspected malicious packages or registry compromise, use this repository's
GitHub **Security > Report a vulnerability** (maintainer: `@qinains`). Enable
private vulnerability reporting in repository settings before opening public
submissions. Do not include credentials in public issues or release URLs.

The registry accepts untrusted JSON and ZIP data, never executable contributions.
Validation uses bounded HTTPS downloads with a 10 MiB cap, per-I/O timeout and
overall deadline, redirect validation on every hop, and no proxy environment or
authorization headers. Only `github.com`, `release-assets.githubusercontent.com`,
and `objects.githubusercontent.com` over HTTPS are allowed. Redirect signatures
on GitHub asset hosts may be necessary; submitted URLs cannot contain queries.
TLS certificate verification remains enabled. ZIPs are read in memory, not
extracted, and their contents and hashes are checked before publication.

Publication CI additionally runs the official `i-plugin-check` against an inert
temporary ZIP. The executable is built from a fixed, reviewed `qinains/i` source
commit, never a contributor-selected plugin repository or revision. It validates
the real package/module ABI via `inspect_package` without invoking Wasm. Calls
use an argument array with no shell and a 30-second timeout; nonzero exit or timeout
stops publication before output is written. Python-only validation without
`--checker` is a preliminary local check, not the production publication gate.

These checks do not establish that plugin code is benign, that source corresponds
to its binary, or that an author is trustworthy. Human source/license/permission
review and host sandboxing are essential. Clients must recheck package integrity,
manifest policy, and permissions before running Wasm.

Protect the default branch and require CODEOWNER review for every change,
especially workflows and validators. A contributor can propose workflow changes;
GitHub branch protection and review, not the workflow alone, enforce that trust
boundary. PR jobs have no deployment credentials and use trusted base validators.
Never enable secrets for forked PR validation or switch to `pull_request_target`
with head code execution. Deploy only from protected `main`.

An upstream release asset replacement fails its pinned digest and blocks a fresh
deploy, leaving the last successful Pages deployment intact. Remove or fix a
compromised entry through a reviewed policy update, deploy, and notify users;
already-installed or locally cached packages are not automatically revoked.
