import copy
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lawcase_sources as sources


class FakeTransport:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.arguments = []

    async def call_tool(self, name, arguments):
        self.arguments.append(arguments)
        return SimpleNamespace(structured_content={"result": next(self.pages)}, is_error=False)


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_pages_continue_until_empty(self):
        transport = FakeTransport([[{"id": "a"}], [{"id": "b"}], []])
        result = await sources.HaikuClient(transport, {"page_size": 10}).list()
        self.assertEqual([x["id"] for x in result], ["a", "b"])
        self.assertEqual([x["offset"] for x in transport.arguments], [0, 1, 2])

    async def test_repeated_page_fails(self):
        with self.assertRaises(sources.SnapshotError):
            await sources.HaikuClient(FakeTransport([[{"id": "a"}]] * 2), {}).list()

    async def test_max_pages_fails(self):
        with self.assertRaises(sources.SnapshotError):
            await sources.HaikuClient(FakeTransport([[{"id": "a"}]]), {"max_pages": 1}).list()

    async def test_bad_page_fails(self):
        with self.assertRaises(sources.SnapshotError):
            await sources.HaikuClient(FakeTransport([[{"uri": "bad"}]]), {}).list()


class SourceTests(unittest.TestCase):
    def test_access_time_changes_do_not_invalidate_read_only_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'a.txt').write_text('Source text')
            real_fstat = sources.os.fstat
            def accessed(fd):
                value = real_fstat(fd)
                names = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
                return SimpleNamespace(**{name: getattr(value, name) for name in names},
                                       st_atime=value.st_atime + 60, st_atime_ns=value.st_atime_ns + 60_000_000_000)
            with patch.object(sources.os, 'fstat', side_effect=accessed):
                result = sources.scan_sources(root)
            self.assertEqual(result['a.txt']['source_sha256'], sources._hash(b'Source text'))

    def test_split_covers_every_character_and_is_deterministic(self):
        text = "ä\n🙂xyz" * 4000
        units = sources.split_text("doc", text)
        self.assertEqual(units, sources.split_text("doc", text))
        self.assertEqual("".join(text[u["start"]:u["end"]] for u in units), text)
        for unit in units:
            self.assertEqual(unit["text_sha256"], sources._hash(text[unit["context_start"]:unit["context_end"]].encode()))
        self.assertEqual(sources.split_text("empty", ""), [])
        with self.assertRaises(ValueError):
            sources.split_text("doc", text, 800, 800)

    def test_reconciliation_accounts_for_all_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a.txt").write_text("a")
            (root / "b.txt").write_text("b")
            (root / "link").symlink_to(root / "a.txt")
            indexed = [{"id": "a", "uri": (root / "a.txt").as_uri()},
                       {"id": "gone", "uri": str(root / "gone.txt")},
                       {"id": "external", "uri": "https://example.org"}]
            records = sources.reconcile(root, sources.scan_sources(root), indexed)
            self.assertCountEqual([r["status"] for r in records], ["ok", "missing", "unmapped", "unindexed", "symlink"])

    def test_mutation_during_scan_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            file = root / "file.txt"
            file.write_text("original")
            original_open = sources.os.open
            def changing_open(path, flags):
                file.write_text("changed while scanning")
                return original_open(path, flags)
            with patch.object(sources.os, "open", changing_open):
                with self.assertRaises(sources.SnapshotError):
                    sources.scan_sources(root)

    def test_duplicate_path_and_duplicate_id(self):
        root = Path("/tmp/source")
        documents = [{"id": "a", "uri": "a.txt"}, {"id": "b", "uri": "a.txt"}]
        self.assertEqual([r["status"] for r in sources.reconcile(root, {}, documents)], ["duplicate", "duplicate"])
        documents[1]["id"] = "a"
        with self.assertRaises(sources.SnapshotError):
            sources.reconcile(root, {}, documents)


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        (self.root / "a.txt").write_text("Original file")
        self.document = {"id": "a", "uri": str(self.root / "a.txt"), "content": "Frozen extracted text " * 1000, "metadata": {}}
        self.index = [{"id": "a", "uri": self.document["uri"], "source": "local", "metadata": {}}]
        self.config = {"source_root": str(self.root)}
        self.get_calls = 0
        self.connections = 0
        self.mutate = None
        test = self
        class Client:
            async def list(self):
                return copy.deepcopy(test.index)
            async def get(self, *_):
                test.get_calls += 1
                if test.mutate:
                    test.mutate(test.get_calls)
                return copy.deepcopy(test.document)
        @asynccontextmanager
        async def connect(_):
            self.connections += 1
            yield Client()
        self.patcher = patch.object(sources, "connect", connect)
        self.patcher.start()

    async def asyncTearDown(self):
        self.patcher.stop()
        for path in self.base.rglob("*"):
            if path.is_dir():
                path.chmod(0o700)
        self.temp.cleanup()

    async def test_snapshot_copies_deterministically_and_never_overwrites(self):
        one, two = self.base / "one", self.base / "two"
        manifest = await sources.snapshot(self.config, one)
        self.assertEqual(manifest, await sources.snapshot(self.config, two))
        self.assertEqual((one / "manifest.json").read_bytes(), (two / "manifest.json").read_bytes())
        self.assertEqual((one / manifest["documents"][0]["text_file"]).read_text(), self.document["content"])
        self.assertEqual(self.connections, 4)
        self.assertTrue(manifest["frozen"])
        self.assertFalse(manifest["ingestion_frozen"])
        self.assertFalse(manifest["source_index_provenance_verified"])
        with self.assertRaises(FileExistsError):
            await sources.snapshot(self.config, one)

    async def test_changed_source_refuses_publication(self):
        self.mutate = lambda count: (self.root / "a.txt").write_text("Changed") if count == 1 else None
        with self.assertRaises(sources.SnapshotError):
            await sources.snapshot(self.config, self.base / "out")
        self.assertFalse((self.base / "out").exists())

    async def test_changed_index_text_refuses_publication(self):
        def mutate(count):
            if count == 2:
                self.document["content"] = "Changed indexed text"
        self.mutate = mutate
        with self.assertRaises(sources.SnapshotError):
            await sources.snapshot(self.config, self.base / "out")
        self.assertFalse((self.base / "out").exists())

    async def test_missing_and_unindexed_are_published_as_incomplete(self):
        (self.root / "a.txt").unlink()
        (self.root / "b.txt").write_text("Not indexed")
        manifest = await sources.snapshot(self.config, self.base / "out")
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["counts"], {"missing": 1, "unindexed": 1})
        self.assertEqual(manifest["units"], [])

    async def test_unmapped_refuses_publication(self):
        self.index[0]["uri"] = "/elsewhere/a.txt"
        with self.assertRaises(sources.SnapshotError):
            await sources.snapshot(self.config, self.base / "out")

    async def test_whitespace_has_no_units_and_outputs_are_private(self):
        self.document["content"] = " \n\t"
        dest = self.base / "out"
        manifest = await sources.snapshot(self.config, dest)
        self.assertEqual(manifest["units"], [])
        self.assertEqual(manifest["documents"][0]["status"], "empty")
        self.assertEqual(manifest["documents"][0]["mime"], "text/plain")
        self.assertEqual(manifest["documents"][0]["size"], len("Original file"))
        self.assertEqual(dest.stat().st_mode & 0o777, 0o500)
        self.assertEqual((dest / "manifest.json").stat().st_mode & 0o777, 0o400)

    async def test_changed_enumeration_refuses_publication(self):
        def mutate(count):
            if count == 1:
                self.index.append({"id": "new", "uri": str(self.root / "new.txt")})
        self.mutate = mutate
        with self.assertRaises(sources.SnapshotError):
            await sources.snapshot(self.config, self.base / "out")
        self.assertFalse((self.base / "out").exists())

    async def test_http_snapshot_refuses_unverifiable_cache(self):
        with self.assertRaisesRegex(sources.SnapshotError, "cache freshness"):
            await sources.snapshot(dict(self.config, mcp_url="http://localhost:8001/mcp"), self.base / "out")

    async def test_snapshot_inside_source_refused(self):
        with self.assertRaises(sources.SnapshotError):
            await sources.snapshot(self.config, self.root / "out")


if __name__ == "__main__":
    unittest.main()
