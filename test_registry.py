import copy
import hashlib
import io
import json
import struct
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import zipfile

import registry


def fixture(change=None, extra=None, compression=zipfile.ZIP_STORED, license_content=b"MIT license"):
    module = b"\0asm\x01\0\0\0"
    entry = {"id": "example.echo", "name": "Echo", "version": "1.0.0", "description": "Echo a message",
             "repository": "https://github.com/qinains/echo", "source_revision": "a" * 40,
             "license": "MIT", "permissions": {"workspace": "none", "storage": False}}
    manifest = {"format_version": 1, "id": entry["id"], "name": entry["name"], "version": entry["version"],
                "runtime": "harness-wasm-v1", "entry": "plugin.wasm", "sha256": hashlib.sha256(module).hexdigest(),
                "permissions": entry["permissions"], "tools": [{"name": "echo", "description": "Echo",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}
    if change:
        change(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("plugin.wasm", module)
        archive.writestr("LICENSE", license_content)
        if extra:
            archive.writestr(*extra)
    data = buffer.getvalue()
    entry["package"] = {"url": "https://github.com/qinains/echo/releases/download/v1.0.0/echo.hplugin.zip",
                        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return entry, data


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.entry, self.data = fixture()

    def test_valid_package(self):
        registry.entry_validate(self.entry)
        registry.package_validate(self.data, self.entry)

    def test_invalid_ids(self):
        for value in ("builtin.echo", "UPPER", "../echo", "a" * 65, "a_b", "", 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                registry.entry_validate(dict(self.entry, id=value))

    def test_repository_boundaries(self):
        for owner, repo in (("a", "x"), ("a" * 39, "x" * 100), ("a-b", "_name.repo-")):
            entry = copy.deepcopy(self.entry)
            entry["repository"] = f"https://github.com/{owner}/{repo}"
            entry["package"]["url"] = f"{entry['repository']}/releases/download/v1/x.hplugin.zip"
            registry.entry_validate(entry)
        for owner, repo in (("a" * 40, "x"), ("a", "x" * 101), ("a_b", "x"),
                            ("-owner", "x"), ("a", "."), ("a", "..")):
            with self.subTest(owner=owner, repo=repo), self.assertRaises(ValueError):
                registry.entry_validate(dict(self.entry, repository=f"https://github.com/{owner}/{repo}"))

    def test_string_utf8_and_control_boundaries(self):
        for field, limit in (("name", 256), ("description", 4096), ("license", 128)):
            value = "\u00e9" * (limit // 2)
            registry.entry_validate(dict(self.entry, **{field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                registry.entry_validate(dict(self.entry, **{field: value + "a"}))
            for code in [*range(9), 11, 12, *range(14, 32), 127]:
                with self.subTest(field=field, code=code), self.assertRaises(ValueError):
                    registry.entry_validate(dict(self.entry, **{field: "valid" + chr(code)}))
            registry.entry_validate(dict(self.entry, **{field: "text\twith\nallowed\rwhitespace"}))

    def test_versions_and_permissions(self):
        for value in ("01.0.0", "1.0", "1.0.0-beta", "4294967296.0.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                registry.entry_validate(dict(self.entry, version=value))
        with self.assertRaises(ValueError):
            registry.entry_validate(dict(self.entry, permissions={"workspace": "none", "storage": 1}))

    def test_invalid_urls(self):
        prefix = "https://github.com/qinains/echo/releases/download/"
        for url in ("http://github.com/qinains/echo/releases/download/v1/x.hplugin.zip",
                    "https://localhost/file.hplugin.zip", prefix + "../x.hplugin.zip",
                    prefix + "%2e%2e/x.hplugin.zip", prefix + "v1/x.hplugin.zip?token=secret",
                    prefix + "v1/x.hplugin.zip#fragment", prefix + "v1/sub/x.hplugin.zip",
                    prefix.replace("qinains/echo", "other/repo") + "v1/x.hplugin.zip",
                    prefix.replace("github.com", "user:pass@github.com") + "v1/x.hplugin.zip",
                    prefix.replace("github.com", "github.com:443") + "v1/x.hplugin.zip"):
            entry = copy.deepcopy(self.entry)
            entry["package"]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                registry.entry_validate(entry)

    def test_redirect_allowlist(self):
        for url in ("http://github.com/x", "https://evil.example/x", "https://github.com.evil.example/x", "https://x@github.com/a"):
            with self.assertRaises(ValueError):
                registry.https_url(url)
        registry.https_url("https://release-assets.githubusercontent.com/a?signature=public")

    def test_download_bound_and_no_proxy(self):
        class Response:
            url = "https://release-assets.githubusercontent.com/asset"
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read1(self, size):
                return b"x" * size

        with patch.object(registry.urllib.request, "build_opener") as build, patch.object(registry, "MAX_PACKAGE", 16):
            build.return_value.open.return_value = Response()
            with self.assertRaisesRegex(ValueError, "10 MiB"):
                registry.download(self.entry["package"]["url"])
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 10)

    def test_download_deadline(self):
        with patch.object(registry.urllib.request, "build_opener") as build, patch.object(registry.time, "monotonic", side_effect=[0, 61]):
            response = build.return_value.open.return_value.__enter__.return_value
            response.url = "https://objects.githubusercontent.com/asset"
            response.status = 200
            with self.assertRaisesRegex(ValueError, "deadline"):
                registry.download(self.entry["package"]["url"])
            response.read1.assert_not_called()

    def test_mismatched_manifest(self):
        for mutate in (lambda m: m.update(id="other"), lambda m: m.update(version="2.0.0"),
                       lambda m: m.update(sha256="0" * 64), lambda m: m.update(runtime="javascript"),
                       lambda m: m.update(permissions={"workspace": "read", "storage": False}),
                       lambda m: m.update(permissions={"workspace": "none", "storage": 0}),
                       lambda m: m.update(tools=[]), lambda m: m["tools"][0].update(parameters={"type": "object", "$ref": "file:///secret"})):
            entry, data = fixture(mutate)
            with self.assertRaises(ValueError):
                registry.package_validate(data, entry)

    def test_corrupt_digest_and_size(self):
        with self.assertRaises(ValueError):
            registry.package_validate(self.data[:-1] + b"x", self.entry)
        with self.assertRaises(ValueError):
            registry.package_validate(self.data + b"x", self.entry)

    def test_extra_traversal_and_duplicate_zip_entries(self):
        for name in ("../outside", "extra", "manifest.json", "nested/plugin.wasm"):
            with self.subTest(name=name):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    entry, data = fixture(extra=(name, b"x"))
                with self.assertRaises(ValueError):
                    registry.package_validate(data, entry)

    def test_zip_limits_and_symlink(self):
        with patch.object(registry, "MAX_MODULE", 7), self.assertRaises(ValueError):
            registry.package_validate(self.data, self.entry)
        info = zipfile.ZipInfo("LICENSE")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.data)) as old, zipfile.ZipFile(buffer, "w") as new:
            for name in ("manifest.json", "plugin.wasm"):
                new.writestr(name, old.read(name))
            new.writestr(info, "/etc/passwd")
        data = buffer.getvalue()
        entry = copy.deepcopy(self.entry)
        entry["package"].update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        with self.assertRaises(ValueError):
            registry.package_validate(data, entry)

    def test_strict_zip_comments_prefix_zip64_and_names(self):
        commented = io.BytesIO(self.data)
        with zipfile.ZipFile(commented, "a") as archive:
            archive.comment = b"comment"
        end = len(self.data) - 22
        directory_size, directory_start = struct.unpack_from("<LL", self.data, end + 12)
        zip64_end = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0,
                                3, 3, directory_size, directory_start)
        zip64_locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
        zip64 = self.data[:end] + zip64_end + zip64_locator + self.data[end:]
        for data in (commented.getvalue(), b"prefix" + self.data, zip64):
            with self.subTest(size=len(data)):
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertEqual(len(archive.infolist()), 3)
                entry = copy.deepcopy(self.entry)
                entry["package"].update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
                with self.assertRaises(ValueError):
                    registry.package_validate(data, entry)
        mismatch = bytearray(self.data)
        mismatch[30] = ord("M")
        with self.assertRaisesRegex(ValueError, "local/central names differ"):
            registry.check_directory(bytes(mismatch))

    def test_compression_bomb(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", b"x" * 65536)
            archive.writestr("plugin.wasm", b"\0asm\x01\0\0\0")
            archive.writestr("LICENSE", "MIT")
        data = buffer.getvalue()
        entry = copy.deepcopy(self.entry)
        entry["package"].update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        with self.assertRaisesRegex(ValueError, "compression"):
            registry.package_validate(data, entry)

    def test_license_rust_size_and_encoding_limits(self):
        for size in (64 * 1024 + 1, 256 * 1024):
            with self.subTest(size=size):
                entry, data = fixture(license_content=b"L" * size)
                registry.package_validate(data, entry)
        entry, data = fixture(license_content=b"L" * (256 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "ZIP size/compression limit"):
            registry.package_validate(data, entry)
        for content in (b"\xff", "\u2003".encode("utf-8")):
            entry, data = fixture(license_content=content)
            with self.assertRaises(ValueError):
                registry.package_validate(data, entry)

    def write(self, root, entry=None, filename="example.echo.json"):
        root.mkdir(exist_ok=True)
        (root / filename).write_text(json.dumps(entry or self.entry))

    def test_build_offline_and_mirror(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root / "extensions")
            index = registry.validate(root / "extensions", output=root / "dist", downloader=lambda url: self.data)
            digest = self.entry["package"]["sha256"]
            self.assertEqual(index["schema_version"], 1)
            self.assertEqual(index["plugins"][0]["package"]["url"], f"{registry.PUBLIC_URL}/packages/{digest}.hplugin.zip")
            self.assertEqual((root / "dist" / "packages" / f"{digest}.hplugin.zip").read_bytes(), self.data)
            self.assertEqual(json.loads((root / "dist/index.json").read_text()), index)

    def test_index_exact_serialized_size_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root / "extensions")
            index = registry.validate(root / "extensions", downloader=lambda url: self.data)
            size = len((json.dumps(index, indent=2, ensure_ascii=True) + "\n").encode("utf-8"))
            with patch.object(registry, "MAX_INDEX", size):
                registry.validate(root / "extensions", output=root / "dist", downloader=lambda url: self.data)
            self.assertEqual((root / "dist/index.json").stat().st_size, size)
            downloader = Mock(return_value=self.data)
            with patch.object(registry, "MAX_INDEX", size - 1), self.assertRaisesRegex(ValueError, "index exceeds"):
                registry.validate(root / "extensions", downloader=downloader)
            downloader.assert_not_called()

    def test_official_checker_success_uses_inert_tempfile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root / "extensions")
            def check(args, **kwargs):
                self.assertEqual(args[0], str((root / "official-checker").resolve()))
                self.assertEqual(Path(args[1]).read_bytes(), self.data)
                self.assertFalse(kwargs["shell"])
                self.assertEqual(kwargs["timeout"], 30)
                return Mock(returncode=0)
            with patch.object(registry.subprocess, "run", side_effect=check) as run:
                registry.validate(root / "extensions", output=root / "dist",
                                  downloader=lambda url: self.data, checker=root / "official-checker")
            self.assertFalse(Path(run.call_args.args[0][1]).exists())
            self.assertTrue((root / "dist/index.json").is_file())

    def test_official_checker_rejection_and_timeout_block_publication(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write(root / "extensions")
                effect = subprocess.TimeoutExpired("official-checker", 30) if timeout else None
                with patch.object(registry.subprocess, "run", return_value=Mock(returncode=1), side_effect=effect), self.assertRaisesRegex(ValueError, "official package checker"):
                    registry.validate(root / "extensions", output=root / "dist",
                                      downloader=lambda url: self.data, checker=root / "official-checker")
                self.assertFalse((root / "dist").exists())

    def test_catalog_500_entries_and_actual_512k_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for i in range(500):
                entry = dict(self.entry, id=f"example.p{i}", description="d" * 4096)
                self.write(root, entry, filename=f"example.p{i}.json")
            self.assertEqual(len(registry.load_entries(root)), 500)
            downloader = Mock(return_value=self.data)
            with self.assertRaisesRegex(ValueError, "index exceeds 512 KiB"):
                registry.validate(root, downloader=downloader)
            downloader.assert_not_called()
            self.write(root, dict(self.entry, id="example.p500"), filename="example.p500.json")
            with self.assertRaisesRegex(ValueError, "catalog entry limit"):
                registry.load_entries(root)

    def test_duplicate_ids_and_json_keys(self):
        with self.assertRaises(ValueError):
            registry.json_load('{"id": 1, "id": 2}')
        with self.assertRaises(UnicodeError):
            registry.json_load('{"id": 1}'.encode("utf-16"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root)
            self.write(root, filename="z.json")
            with self.assertRaisesRegex(ValueError, "duplicate plugin id"):
                registry.load_entries(root)

    def test_immutable_versions_deletion_and_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base, head = root / "base", root / "head"
            self.write(base)
            self.write(head, dict(self.entry, description="changed"))
            with self.assertRaisesRegex(ValueError, "immutable"):
                registry.validate(head, base, downloader=lambda url: self.data)
            self.write(head, dict(self.entry, version="0.9.0"))
            with self.assertRaisesRegex(ValueError, "rollback"):
                registry.validate(head, base, downloader=lambda url: self.data)
            (head / "example.echo.json").unlink()
            with self.assertRaisesRegex(ValueError, "deleting"):
                registry.validate(head, base, downloader=lambda url: self.data)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write(root)
            (root / "alias.json").symlink_to(root / "example.echo.json")
            with self.assertRaises(ValueError):
                registry.load_entries(root)


if __name__ == "__main__":
    unittest.main()
