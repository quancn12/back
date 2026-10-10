"""Source storage must never write outside its root, and must not move files.

A source key is caller-controlled: the upload route takes it as a query
parameter. Before this was fixed, ``a/../../x`` passed through ``safe_key``
unchanged and the PDF was written outside the storage directory.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.destination import Destination
from app.services import source_storage as storage_module
from app.services import travel_ingestion as ingestion
from app.services.source_storage import LocalSourceStorage, safe_key

TRAVERSAL_KEYS = (
    "a/../../escaped",
    "a/b/../../../escaped",
    "../../escaped",
    "..",
    "../",
    "a/./../..//escaped",
    "a/.../b",
    "/etc/passwd",
    # Ingestion turns backslashes into slashes before storage sees the key, so
    # a Windows-style path is exactly as dangerous as a POSIX one.
    "a\\..\\..\\escaped",
)


class SafeKeyTests(unittest.TestCase):
    def test_no_segment_made_only_of_dots_survives(self):
        for key in TRAVERSAL_KEYS:
            with self.subTest(key=key):
                produced = safe_key(ingestion._source_key(key))
                self.assertNotIn("..", produced.split("/"))
                self.assertFalse(any(set(part) == {"."} for part in produced.split("/")))
                self.assertFalse(produced.startswith("/"))

    def test_real_keys_come_out_exactly_as_before(self):
        # Values produced by the old implementation. Existing documents live
        # under these paths, so any change here would orphan stored files.
        expected = {
            "hồ thác bà/hvnt 1n hồ thác bà.pdf": "ho-thac-ba/hvnt-1n-ho-thac-ba.pdf",
            "demo:data/demo/corpus/demo_facts.pdf": "demo-data/demo/corpus/demo_facts.pdf",
            "folder:demo_facts.pdf": "folder-demo_facts.pdf",
            "upload:ha-noi:tour-ha-noi-2n1d.pdf": "upload-ha-noi-tour-ha-noi-2n1d.pdf",
            "đà nẵng 3n2đ.pdf": "da-nang-3n2d.pdf",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(safe_key(key), value)

    def test_an_empty_result_still_has_a_name(self):
        for key in ("", "..", "/", "./.", "---"):
            with self.subTest(key=key):
                self.assertEqual(safe_key(key), "document")


class StoreContainmentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.root = self.base / "storage"
        self.root.mkdir()
        self.pdf = self.base / "source.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 test")
        self.store = LocalSourceStorage(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def _files_outside_root(self):
        root = self.root.resolve()
        return [
            path
            for path in self.base.rglob("*.pdf")
            if path != self.pdf and not path.resolve().is_relative_to(root)
        ]

    def test_every_traversal_key_lands_inside_the_root(self):
        for key in TRAVERSAL_KEYS:
            with self.subTest(key=key):
                stored = self.store.store(self.pdf, ingestion._source_key(key), "a" * 8)
                self.assertTrue(stored.resolve().is_relative_to(self.root.resolve()))
        self.assertEqual(self._files_outside_root(), [])

    def test_containment_holds_even_if_safe_key_lets_something_through(self):
        # Defence in depth: the resolved path is checked independently of
        # safe_key, so a future regression there still cannot write outside.
        with patch.object(storage_module, "safe_key", lambda value: value):
            with self.assertRaises(ValueError):
                self.store.store(self.pdf, "../escaped", "b" * 8)
        self.assertEqual(self._files_outside_root(), [])
        self.assertFalse((self.base / "escaped").exists(), "no directory may be created outside")

    def test_a_symlink_inside_the_root_cannot_be_used_to_leave_it(self):
        outside = self.base / "outside"
        outside.mkdir()
        try:
            os.symlink(outside, self.root / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this machine does not allow creating symlinks")
        with self.assertRaises(ValueError):
            self.store.store(self.pdf, "link/sub", "c" * 8)
        self.assertEqual(list(outside.rglob("*")), [])


class IngestionPathTests(unittest.TestCase):
    """The route the upload endpoint actually takes: enqueue_pdf_path."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.engine = create_engine(
            f"sqlite:///{(self.base / 'ingest.db').as_posix()}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.destination = Destination(
            slug="ha-noi",
            name="Hà Nội",
            destination_type="city",
            country_code="VN",
            country_name="Việt Nam",
            province_name="Hà Nội",
        )
        self.db.add(self.destination)
        self.db.commit()
        self.root = self.base / "sources"
        self.pdf = self.base / "upload.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 upload")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def test_a_hostile_source_key_from_the_upload_route_stays_inside(self):
        storage = LocalSourceStorage(self.root)
        with patch.object(ingestion, "source_storage", storage):
            ingestion.enqueue_pdf_path(
                path=self.pdf,
                destination=self.destination,
                db=self.db,
                source_key="a/../../../escaped",
                filename="upload.pdf",
            )
        written = [p for p in self.base.rglob("*.pdf") if p != self.pdf]
        self.assertTrue(written, "the upload should still be stored")
        for path in written:
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()), path)


if __name__ == "__main__":
    unittest.main()
