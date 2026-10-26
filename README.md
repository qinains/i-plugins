# i-plugins

Public, pull-request-driven registry for i / lninl browser plugins. Submit metadata,
pass validation, obtain maintainer review, and merge. GitHub Actions mirrors the
validated immutable release ZIP and deploys a new catalog to GitHub Pages.
The app at <https://i.lninl.com/app> reads this catalog without an app rebuild,
cross-repository token, server, or database.

- Public API: <https://qinains.github.io/i-plugins/index.json>
- Repository: <https://github.com/qinains/i-plugins>
- Browse plugins: <https://i.lninl.com/plugins/>
- Initial examples: `example.word-count` (workspace read) and
  `example.json-format` (workspace read/write). Install and grant access explicitly;
  catalog inclusion is not publisher verification or a security guarantee.
- Python 3.10+; Python code uses only the standard library. Publication CI also
  builds the pinned official Rust package checker. No plugin code is executed.

## Local verification

```sh
python3 -m unittest -v
python3 registry.py
python3 registry.py --output dist
# Full publication validation with the trusted official checker:
python3 registry.py --output dist --checker /path/to/i-plugin-check
# Compare a proposed extensions directory to a trusted checkout:
python3 registry.py --extensions extensions --base ../trusted/extensions
```

Output: `dist/index.json` and `dist/packages/<sha256>.hplugin.zip`.
The builder completes validation before writing its output. Build into a clean,
dedicated output directory. Only `dist` is uploaded to Pages.

## Entry schema (API 1)

Each `extensions/<id>.json` contains exactly these fields (no unknown or duplicate
JSON keys). This example is illustrative, not a downloadable catalog entry:

```json
{
  "id": "example.echo",
  "name": "Echo",
  "version": "1.0.0",
  "description": "Echo a message",
  "repository": "https://github.com/owner/repo",
  "source_revision": "0123456789abcdef0123456789abcdef01234567",
  "license": "MIT",
  "package": {
    "url": "https://github.com/owner/repo/releases/download/v1.0.0/echo.hplugin.zip",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "size": 12345
  },
  "permissions": {"workspace": "none", "storage": false}
}
```

Normative entry constraints enforced by `registry.py`:

| Field | Constraint |
| --- | --- |
| `id` | `^[a-z][a-z0-9.-]{0,63}$`; `builtin.*` reserved; filename equals ID |
| `name` | Nonblank string, at most 256 UTF-8 bytes |
| `version` | Exactly `N.N.N`; no leading zeroes; each part is unsigned 32-bit |
| `description` | Nonblank string, at most 4096 UTF-8 bytes |
| `repository` | Exactly `https://github.com/<owner>/<repo>`; owner matches `[A-Za-z0-9][A-Za-z0-9-]{0,38}`, repo matches `[A-Za-z0-9_.-]{1,100}` (not `.`/`..`); at most 256 characters; no trailing slash/query |
| `source_revision` | Exactly 40 lowercase hexadecimal characters; audited source commit |
| `license` | Nonblank license identifier/expression, at most 128 UTF-8 bytes |
| `package.url` | Same repository's GitHub release asset URL as above; ASCII tag/filename; `.hplugin.zip` suffix; no credentials, port, query, fragment, escapes, or traversal |
| `package.sha256` | Exactly 64 lowercase hexadecimal characters; hash of ZIP bytes |
| `package.size` | Integer ZIP byte size, 1 through 10 MiB |
| `permissions.workspace` | `none`, `read`, or `read-write` |
| `permissions.storage` | Boolean |

Strings reject control characters U+0000..0008, U+000B, U+000C,
U+000E..001F, and U+007F. Text lengths above are measured in UTF-8 bytes,
not Unicode characters.

The catalog is capped at 500 entries and 100 MiB of declared package data.
The actual serialized public `index.json`, including indentation and its final
newline, must not exceed 512 KiB. This is checked even in validation-only mode,
before downloading packages, so a passing PR cannot produce an oversized index.
IDs are unique. CI comparison with the trusted base rejects deletion, rollback,
and **any metadata change at the same ID/version**, including a changed digest.
An update replaces the entry with a higher version. Git history preserves old
registrations; the current API/mirror contains current entries only, not a
permanent archive of old versions.

### Package format

The ZIP must contain exactly three regular, unencrypted files at its root:

- `manifest.json`: at most 64 KiB, UTF-8 JSON, exact Harness manifest fields.
- `plugin.wasm`: at most 8 MiB; version-1 Wasm header; SHA-256 equals manifest `sha256`.
- `LICENSE`: nonblank UTF-8 text, at most 256 KiB; maintainers review actual license terms.

The ZIP must have an unprefixed archive, a final 22-byte EOCD with no comment,
one disk, exactly three central-directory entries, no ZIP64 directory records,
and matching local/central filenames. Only stored/deflated entries are supported; expanded-to-compressed ratio must
not exceed 200:1. No extra files, duplicates, directories, symlinks, or traversal.
The manifest has `format_version: 1`, `runtime: "harness-wasm-v1"`,
`entry: "plugin.wasm"`, and exactly matches registered `id`, `name`, `version`,
and `permissions`. It declares 1..16 unique tools with names matching
`[a-z][a-z0-9_]{0,31}`, descriptions up to 4096 bytes, and object parameter
schemas. Supported schema types are object/array/string/integer/number/boolean;
unknown keywords, invalid bounds, and excessive depth/node count are rejected.
Local Python-only validation checks container, metadata, schema shape, and Wasm
magic; it is not a substitute for full publication validation. Both CI paths
require `--checker` pointing to the official `i-plugin-check`, built from an exact
maintainer-pinned `qinains/i` commit. It calls `inspect_package` without executing
Wasm, including the host's ABI/module validation. A missing ABI, checker failure,
or 30-second timeout blocks publication. The checker path cannot be specified by
plugin metadata. The host remains the final installation/runtime authority;
neither checker establishes that code is benign.

### Public response

```json
{"schema_version": 1, "plugins": []}
```

Nonempty `plugins` entries retain the fields above, but `package.url` is rewritten
to `https://qinains.github.io/i-plugins/packages/<sha256>.hplugin.zip`.
ZIP hash and size are unchanged. Clients must validate both before installation,
validate manifest/permissions again, and require user approval. The catalog is
public HTTPS JSON, not executable JavaScript. GitHub Pages serves public assets
cross-origin; consumers should fetch the index and ZIP directly.

## One-time GitHub setup

1. Create the public repository `qinains/i-plugins` from this directory, default
   branch `main`, and enable Actions.
2. In **Settings > Pages > Build and deployment**, select **GitHub Actions**.
3. Protect `main`: require the `validate-pr` check, a CODEOWNER approval, resolved
   discussions, and no force pushes. Restrict direct pushes and workflow changes.
4. Configure the `github-pages` environment to permit deployment from `main` only.
5. Merge a reviewed PR or run the workflow on `main`; inspect the public index.

PR validation uses the Python validator and tests from the **base SHA**, reading only
proposed metadata and inert package bytes from the head. Proposed Python files,
Wasm, plugin source repositories, install hooks, and plugin build scripts are
never executed. Separately, CI builds only the pinned official checker source
with Rust 1.95.0 and `cargo build --locked -p i-wasm-package --features cli
--bin i-plugin-check`. No submitted plugin source is built. The subprocess reads
an inert temporary ZIP with no shell; it must succeed before any output is written.
Changes to validator/workflow policy need a separate maintainer-reviewed PR;
new policies become effective for subsequent PRs after merging. All actions are
commit-pinned. No `pull_request_target`, PAT, or application repository access is
used. Only the separate post-merge deployment job obtains Pages/OIDC write scope.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
