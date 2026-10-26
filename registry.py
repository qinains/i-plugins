#!/usr/bin/env python3
"""Validate inert plugin packages and produce the public API 1 catalog."""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

MAX_PACKAGE = 10 * 1024 * 1024
MAX_MODULE = 8 * 1024 * 1024
MAX_INDEX = 512 * 1024
PUBLIC_URL = "https://qinains.github.io/i-plugins"
ID = re.compile(r"[a-z][a-z0-9.-]{0,63}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REPOSITORY = re.compile(r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})\Z")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ALLOWED_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, keys, label):
    require(type(value) is dict and set(value) == set(keys.split()), f"invalid {label} fields")


def string(value, limit, label):
    require(type(value) is str and value.strip() and not CONTROL.search(value)
            and len(value.encode()) <= limit, f"invalid {label}")


def json_load(data):
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def permissions(value):
    fields(value, "workspace storage", "permissions")
    require(value["workspace"] in ("none", "read", "read-write") and type(value["storage"]) is bool,
            "invalid permissions")


def version(value):
    require(type(value) is str and re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value), "invalid version")
    require(all(len(p) <= 10 and int(p) <= 4294967295 for p in value.split(".")), "version exceeds u32")


def https_url(url):
    require(type(url) is str and url.isascii() and not any(c.isspace() or ord(c) < 32 for c in url), "invalid URL")
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS and
            parsed.netloc == parsed.hostname and not parsed.fragment, "unapproved HTTPS URL")
    return parsed


def entry_validate(entry):
    fields(entry, "id name version description repository source_revision license package permissions", "entry")
    require(type(entry["id"]) is str and ID.fullmatch(entry["id"]) and not entry["id"].startswith("builtin."), "invalid or reserved id")
    string(entry["name"], 256, "name")
    string(entry["description"], 4096, "description")
    string(entry["license"], 128, "license")
    version(entry["version"])
    repo = REPOSITORY.fullmatch(entry["repository"]) if type(entry["repository"]) is str else None
    require(repo is not None and len(entry["repository"]) <= 256
            and all(x not in (".", "..") for x in repo.groups()), "invalid repository")
    require(type(entry["source_revision"]) is str and re.fullmatch(r"[0-9a-f]{40}", entry["source_revision"]), "invalid source_revision")
    permissions(entry["permissions"])
    package = entry["package"]
    fields(package, "url sha256 size", "package")
    require(type(package["sha256"]) is str and DIGEST.fullmatch(package["sha256"]), "invalid package digest")
    require(type(package["size"]) is int and 0 < package["size"] <= MAX_PACKAGE, "invalid package size")
    url = https_url(package["url"])
    prefix = f"/{repo[1]}/{repo[2]}/releases/download/"
    require(url.hostname == "github.com" and not url.query and "%" not in url.path and "\\" not in url.path
            and url.path.startswith(prefix), "invalid package release URL")
    pieces = url.path[len(prefix):].split("/")
    require(len(pieces) == 2 and all(re.fullmatch(r"[A-Za-z0-9_.+-]+", p) and p not in (".", "..") for p in pieces)
            and pieces[1].endswith(".hplugin.zip"), "invalid release tag/filename")


def schema_validate(schema):
    nodes = [0]
    def walk(node, depth):
        nodes[0] += 1
        require(type(node) is dict and depth <= 16 and nodes[0] <= 1024, "schema complexity/type limit")
        kind = node.get("type")
        extras = {"object": {"properties", "required", "additionalProperties"}, "array": {"items", "minItems", "maxItems"},
                  "string": {"minLength", "maxLength"}, "integer": {"minimum", "maximum"}, "number": {"minimum", "maximum"}, "boolean": set()}
        require(type(kind) is str and kind in extras and set(node) <= extras[kind] | {"type", "description", "title", "enum"}, "unsupported schema")
        for key in ("description", "title"):
            if key in node:
                require(type(node[key]) is str and len(node[key].encode()) <= 4096, "invalid schema text")
        if "enum" in node:
            values = node["enum"]
            require(type(values) is list and 1 <= len(values) <= 256 and len({json.dumps(v, sort_keys=True) for v in values}) == len(values), "invalid enum")
        if kind == "object":
            props = node.get("properties", {})
            require(type(props) is dict, "invalid properties")
            for name, child in props.items():
                string(name, 128, "property name")
                walk(child, depth + 1)
            required = node.get("required", [])
            require(type(required) is list and all(type(x) is str and x in props for x in required) and len(set(required)) == len(required), "invalid required")
            require("additionalProperties" not in node or type(node["additionalProperties"]) is bool, "invalid additionalProperties")
        if kind == "array":
            walk(node.get("items"), depth + 1)
        bounds = {"array": ("minItems", "maxItems"), "string": ("minLength", "maxLength"), "number": ("minimum", "maximum"), "integer": ("minimum", "maximum")}
        if kind in bounds:
            low, high = bounds[kind]
            for key in (low, high):
                if key in node:
                    n = node[key]
                    require(type(n) in (int, float) and math.isfinite(n) and (kind not in ("array", "string") or type(n) is int and 0 <= n <= 2**64 - 1), "invalid bound")
            require(low not in node or high not in node or node[low] <= node[high], "reversed bounds")
    require(type(schema) is dict and schema.get("type") == "object", "parameters must be object schema")
    walk(schema, 0)


def manifest_validate(manifest, entry, module):
    fields(manifest, "format_version id name version runtime entry sha256 permissions tools", "manifest")
    permissions(manifest["permissions"])
    require(type(manifest["format_version"]) is int and manifest["format_version"] == 1 and manifest["runtime"] == "harness-wasm-v1" and manifest["entry"] == "plugin.wasm", "unsupported runtime")
    for key in ("id", "name", "version", "permissions"):
        require(manifest[key] == entry[key], f"manifest {key} mismatch")
    require(module[:8] == b"\0asm\x01\0\0\0", "invalid Wasm header")
    require(manifest["sha256"] == hashlib.sha256(module).hexdigest(), "module digest mismatch")
    tools = manifest["tools"]
    require(type(tools) is list and 1 <= len(tools) <= 16, "invalid tools count")
    names = set()
    for tool in tools:
        fields(tool, "name description parameters", "tool")
        name = tool["name"]
        require(type(name) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) and name not in names, "invalid/duplicate tool name")
        names.add(name)
        string(tool["description"], 4096, "tool description")
        schema_validate(tool["parameters"])


def check_directory(data):
    def number(at, width):
        require(0 <= at and at + width <= len(data), "truncated ZIP directory")
        return int.from_bytes(data[at:at + width], "little")

    require(len(data) >= 22, "truncated ZIP")
    end = len(data) - 22
    require(data[end:end + 4] == b"PK\x05\x06" and number(end + 20, 2) == 0,
            "ZIP comments and ZIP64 are not supported")
    require(number(end + 4, 2) == number(end + 6, 2) == 0 and
            number(end + 8, 2) == number(end + 10, 2) == 3, "ZIP must contain three files on one disk")
    start, size = number(end + 16, 4), number(end + 12, 4)
    require(start + size == end, "invalid ZIP directory extent or ZIP64")
    pos, names, offsets = start, set(), []
    for _ in range(3):
        require(data[pos:pos + 4] == b"PK\x01\x02", "invalid ZIP central entry")
        n, extra, comment = number(pos + 28, 2), number(pos + 30, 2), number(pos + 32, 2)
        next_pos = pos + 46 + n + extra + comment
        require(next_pos <= end, "truncated ZIP central entry")
        name = data[pos + 46:pos + 46 + n]
        require(name in (b"manifest.json", b"plugin.wasm", b"LICENSE") and name not in names,
                "unexpected or duplicate ZIP entry")
        names.add(name)
        local = number(pos + 42, 4)
        require(local < start and data[local:local + 4] == b"PK\x03\x04", "invalid ZIP local header")
        local_n = number(local + 26, 2)
        require(local + 30 + local_n <= start and data[local + 30:local + 30 + local_n] == name,
                "ZIP local/central names differ")
        offsets.append(local)
        pos = next_pos
    require(pos == end and min(offsets) == 0, "unexpected ZIP directory data or prefix")
    return start


def package_validate(data, entry):
    package = entry["package"]
    require(type(data) is bytes and len(data) <= MAX_PACKAGE and len(data) == package["size"], "package size mismatch")
    require(hashlib.sha256(data).hexdigest() == package["sha256"], "package digest mismatch")
    directory_start = check_directory(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            require(archive.start_dir == directory_start, "ZIP archive offset must be zero")
            infos = archive.infolist()
            require(len(infos) == 3 and {i.filename for i in infos} == {"manifest.json", "plugin.wasm", "LICENSE"}, "ZIP must contain exactly manifest.json, plugin.wasm, LICENSE")
            limits = {"manifest.json": 65536, "plugin.wasm": MAX_MODULE, "LICENSE": 256 * 1024}
            contents = {}
            for info in infos:
                mode = info.external_attr >> 16
                require(info.orig_filename == info.filename and not info.flag_bits & (1 | 64) and
                        (not stat.S_IFMT(mode) or stat.S_ISREG(mode)), "encrypted or non-regular ZIP entry")
                require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) and
                        0 < info.file_size <= limits[info.filename] and info.file_size <= max(info.compress_size, 1) * 200, "ZIP size/compression limit")
                with archive.open(info) as stream:
                    contents[info.filename] = stream.read(limits[info.filename] + 1)
                require(len(contents[info.filename]) == info.file_size, "ZIP inflated size mismatch")
            require(contents["LICENSE"].decode("utf-8").strip(), "empty LICENSE")
            manifest_validate(json_load(contents["manifest.json"]), entry, contents["plugin.wasm"])
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError(f"invalid ZIP: {error}") from error


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url):
    https_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect())
    deadline = time.monotonic() + 60
    with opener.open(urllib.request.Request(url, headers={"User-Agent": "i-plugins-registry/1"}), timeout=10) as response:
        https_url(response.url)
        require(response.status == 200, "download failed")
        chunks, size = [], 0
        while True:
            require(time.monotonic() < deadline, "download deadline exceeded")
            chunk = response.read1(min(65536, MAX_PACKAGE + 1 - size))
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            require(size <= MAX_PACKAGE, "download exceeds 10 MiB")
            chunks.append(chunk)


def load_entries(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "extensions must be a real directory")
    entries = []
    ids = set()
    for path in sorted(root.iterdir()):
        if path.name == ".gitkeep":
            continue
        require(path.suffix == ".json" and path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536, "unexpected extensions file")
        entry = json_load(path.read_bytes())
        entry_validate(entry)
        require(entry["id"] not in ids, "duplicate plugin id")
        require(path.name == entry["id"] + ".json", "filename must match id")
        ids.add(entry["id"])
        entries.append(entry)
        require(len(entries) <= 500, "catalog entry limit exceeded")
    return entries


def validate(root, base=None, output=None, downloader=download, checker=None):
    entries = load_entries(root)
    require(sum(e["package"]["size"] for e in entries) <= 100 * 1024 * 1024, "catalog exceeds 100 MiB")
    if base is not None:
        current = {e["id"]: e for e in entries}
        for old in load_entries(base):
            require(old["id"] in current, "deleting plugins requires an explicit maintainer policy change")
            new = current[old["id"]]
            require(new["version"] != old["version"] or new == old, "published version is immutable; increment version")
            require(tuple(map(int, new["version"].split("."))) >= tuple(map(int, old["version"].split("."))), "version rollback forbidden")
    public = [dict(entry, package=dict(entry["package"],
              url=f"{PUBLIC_URL}/packages/{entry['package']['sha256']}.hplugin.zip")) for entry in entries]
    index = {"schema_version": 1, "plugins": public}
    encoded_index = (json.dumps(index, indent=2, ensure_ascii=True) + "\n").encode("utf-8")
    require(len(encoded_index) <= MAX_INDEX, "public index exceeds 512 KiB")
    packages = {}
    for entry in entries:
        data = downloader(entry["package"]["url"])
        package_validate(data, entry)
        if checker is not None:
            with tempfile.TemporaryDirectory(prefix="i-plugin-check-") as temp:
                package_path = Path(temp) / "package.hplugin.zip"
                package_path.write_bytes(data)
                try:
                    result = subprocess.run([str(Path(checker).resolve()), str(package_path)],
                                            shell=False, timeout=30, check=False,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except subprocess.TimeoutExpired as error:
                    raise ValueError("official package checker timed out") from error
                require(result.returncode == 0, f"official package checker rejected {entry['id']}")
        digest = entry["package"]["sha256"]
        packages[digest] = data
    if output is not None:
        output = Path(output)
        require(not output.is_symlink(), "output cannot be symlink")
        output.mkdir(parents=True, exist_ok=True)
        package_dir = output / "packages"
        require(not package_dir.is_symlink(), "packages cannot be symlink")
        if package_dir.exists():
            shutil.rmtree(package_dir)
        package_dir.mkdir()
        for digest, data in packages.items():
            (package_dir / f"{digest}.hplugin.zip").write_bytes(data)
        index_path = output / "index.json"
        require(not index_path.is_symlink(), "index cannot be symlink")
        index_path.write_bytes(encoded_index)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extensions", default="extensions")
    parser.add_argument("--base", help="trusted base extensions directory; enforce no deletion, mutation, rollback")
    parser.add_argument("--output", help="build directory, normally dist")
    parser.add_argument("--checker", help="trusted official i-plugin-check executable (required in publication CI)")
    args = parser.parse_args()
    try:
        index = validate(args.extensions, args.base, args.output, checker=args.checker)
    except (ValueError, OSError, KeyError, TypeError, RecursionError, urllib.error.URLError) as error:
        parser.exit(1, f"registry: {error}\n")
    print(f"Validated {len(index['plugins'])} plugins" + (f"; built {args.output}/index.json" if args.output else ""))


if __name__ == "__main__":
    main()
