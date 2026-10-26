# Contributing a plugin

1. Publish your source, license, and reproducible build instructions in a public
   GitHub repository. Identify the exact source commit used to build the module.
2. Build a Harness Wasm plugin locally using the application's SDK/protocol.
   Package only `manifest.json`, `plugin.wasm`, and `LICENSE` in the ZIP root.
   The manifest's `sha256` is the module hash, not the ZIP hash.
3. Publish `<name>.hplugin.zip` as a public GitHub Release asset. Do not replace
   a published artifact. Private assets, arbitrary hosts, and authenticated URLs
   are not supported. Use an ASCII tag such as `v1.0.0`.
4. Compute the ZIP SHA-256 and byte size, for example:

   ```sh
   shasum -a 256 echo.hplugin.zip
   wc -c < echo.hplugin.zip
   ```

5. Fork this repository and add `extensions/<id>.json` following the README
   schema. Request only necessary permissions, explain them in the PR, and link
   the release and exact source revision. Do not submit the ZIP itself here.
6. Run `python3 -m unittest -v` and `python3 registry.py`. Open a pull request.
7. Maintainers review provenance, licensing, permissions, and source behavior.
   After approval and merge, Pages publishes the entry for the app's next catalog
   refresh. Validation failures block publication of the whole catalog.

Updates must increase the three-part version; a published ID/version and all its
metadata are immutable. A source commit is a provenance declaration, not an
automatically proven reproducible-build attestation. CI never builds your plugin.
For removals, malicious artifacts, or license disputes contact the maintainer;
removal requires an explicit reviewed policy change, not an ordinary metadata PR.

Registry code is MIT licensed. Plugin authors retain ownership and independently
license their code and packages; listing here does not relicense their packages.
