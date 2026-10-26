## Plugin

- Plugin ID and version:
- Source repository and exact source commit:
- Release/package URL:
- License:
- Why each workspace/storage permission is necessary:

## Checklist

- [ ] I have permission to distribute this code and included its LICENSE.
- [ ] The release ZIP contains only manifest.json, plugin.wasm, LICENSE.
- [ ] The manifest matches this entry and hashes the actual plugin.wasm.
- [ ] Package SHA-256 and byte size match the uploaded release artifact.
- [ ] I incremented the version; published versions are immutable.
- [ ] I ran `python3 -m unittest -v` and `python3 registry.py`.
- [ ] This PR changes only the intended extensions metadata (explain exceptions).
