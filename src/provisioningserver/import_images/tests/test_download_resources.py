# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `provisioningserver.import_images.download_resources`."""

__all__ = []

from datetime import datetime
import hashlib
import os
import random
import tarfile
from unittest import mock

from maastesting.factory import factory
from maastesting.matchers import (
    MockCalledOnceWith,
    MockCalledWith,
    MockNotCalled,
)
from maastesting.testcase import MAASTestCase
from provisioningserver.config import DEFAULT_IMAGES_URL
from provisioningserver.import_images import download_resources
from provisioningserver.import_images.product_mapping import ProductMapping
from provisioningserver.utils.fs import tempdir
from simplestreams.contentsource import ChecksummingContentSource
from simplestreams.objectstores import FileStore


class MockDateTime(mock.MagicMock):
    """A class for faking datetimes."""

    _utcnow = datetime.utcnow()

    @classmethod
    def utcnow(cls):
        return cls._utcnow


class TestDownloadAllBootResources(MAASTestCase):
    """Tests for `download_all_boot_resources`()."""

    def test_returns_snapshot_path(self):
        self.patch(download_resources, 'datetime', MockDateTime)
        storage_path = self.make_dir()
        expected_path = os.path.join(
            storage_path,
            'snapshot-%s' % MockDateTime._utcnow.strftime('%Y%m%d-%H%M%S'))
        self.assertEqual(
            expected_path,
            download_resources.download_all_boot_resources(
                sources=[], storage_path=storage_path,
                product_mapping=None))

    def test_calls_download_boot_resources(self):
        self.patch(download_resources, 'datetime', MockDateTime)
        storage_path = self.make_dir()
        snapshot_path = download_resources.compose_snapshot_path(
            storage_path)
        cache_path = os.path.join(storage_path, 'cache')
        file_store = FileStore(cache_path)
        source = {
            'url': 'http://example.com',
            'keyring': self.make_file("keyring"),
            }
        product_mapping = ProductMapping()
        fake = self.patch(download_resources, 'download_boot_resources')
        download_resources.download_all_boot_resources(
            sources=[source], storage_path=storage_path,
            product_mapping=product_mapping, store=file_store)
        self.assertThat(
            fake,
            MockCalledWith(
                source['url'], file_store, snapshot_path, product_mapping,
                keyring_file=source['keyring']))


class TestDownloadBootResources(MAASTestCase):
    """Tests for `download_boot_resources()`."""

    def test_syncs_repo(self):
        fake_sync = self.patch(download_resources.RepoWriter, 'sync')
        storage_path = self.make_dir()
        snapshot_path = self.make_dir()
        cache_path = os.path.join(storage_path, 'cache')
        file_store = FileStore(cache_path)
        source_url = DEFAULT_IMAGES_URL

        download_resources.download_boot_resources(
            source_url, file_store, snapshot_path, None, None)
        self.assertEqual(1, len(fake_sync.mock_calls))


class TestComposeSnapshotPath(MAASTestCase):
    """Tests for `compose_snapshot_path`()."""

    def test_returns_path_under_storage_path(self):
        self.patch(download_resources, 'datetime', MockDateTime)
        storage_path = self.make_dir()
        expected_path = os.path.join(
            storage_path,
            'snapshot-%s' % MockDateTime._utcnow.strftime('%Y%m%d-%H%M%S'))
        self.assertEqual(
            expected_path,
            download_resources.compose_snapshot_path(storage_path))


class TestExtractArchiveTar(MAASTestCase):
    """Tests for `extract_archive_Tar`()."""

    def get_file_info(self, filename):
        sha256 = hashlib.sha256()
        size = 0
        with open(filename, 'rb') as f:
            for chunk in iter(lambda: f.read(2**15), b''):
                sha256.update(chunk)
                size += len(chunk)
        return sha256.hexdigest(), size

    def make_tar_xz(self, path):
        tar_xz_path = os.path.join(
            path, factory.make_name('archive') + '.tar.xz')
        files = {}
        with tarfile.open(tar_xz_path, 'w:xz') as tar:
            with tempdir() as tmp:
                for _ in range(3):
                    f = factory.make_file(tmp)
                    tar_path = os.path.basename(f)
                    tar.add(f, tar_path)
                    files[tar_path] = self.get_file_info(f)
                subdir = os.path.join(tmp, 'subdir')
                os.makedirs(subdir)
                for _ in range(3):
                    f = factory.make_file(subdir)
                    tar_path = f[len(tmp) + 1:]
                    tar.add(f, tar_path)
                    files[tar_path] = self.get_file_info(f)
        return tar_xz_path, files

    def test_extracts_files(self):
        with tempdir() as cache_dir:
            store = FileStore(cache_dir)
            tar_xz, files = self.make_tar_xz(cache_dir)
            sha256, size = self.get_file_info(tar_xz)
            checksums = {'sha256': sha256}
            with open(tar_xz, 'rb') as f:
                content_source = ChecksummingContentSource(f, checksums, size)
                cached_files = download_resources.extract_archive_tar(
                    store, os.path.basename(tar_xz), sha256, checksums, size,
                    content_source)
                for f, info in files.items():
                    cached_file = os.path.join(
                        cache_dir, '%s-%s' % (f, sha256))
                    expected_cached_file = (cached_file, f)
                    self.assertIn(expected_cached_file, cached_files)
                    self.assertTrue(os.path.exists(cached_file))
                    self.assertEqual(info, self.get_file_info(cached_file))

    def test_returns_files_from_cache(self):
        with tempdir() as cache_dir:
            store = FileStore(cache_dir)
            tar_xz, files = self.make_tar_xz(cache_dir)
            sha256, size = self.get_file_info(tar_xz)
            checksums = {'sha256': sha256}
            with open(tar_xz, 'rb') as f:
                content_source = ChecksummingContentSource(f, checksums, size)
                download_resources.extract_archive_tar(
                    store, os.path.basename(tar_xz), sha256, checksums, size,
                    content_source)
                mocked_tar = self.patch(download_resources.tarfile, 'open')
                cached_files = download_resources.extract_archive_tar(
                    store, os.path.basename(tar_xz), sha256, checksums, size,
                    content_source)
                self.assertThat(mocked_tar, MockNotCalled())
                for f, info in files.items():
                    cached_file = os.path.join(
                        cache_dir, '%s-%s' % (f, sha256))
                    expected_cached_file = (cached_file, f)
                    self.assertIn(expected_cached_file, cached_files)


class TestRepoWriter(MAASTestCase):
    """Tests for `RepoWriter`."""

    def make_product(self, ftype=None):
        if ftype is None:
            ftype = factory.make_name('ftype')
        return {
            'sha256': factory.make_name('sha256'),
            'size': random.randint(2, 2**16),
            'ftype': ftype,
            'path': '/path/to/%s' % factory.make_name('filename'),
            'os': factory.make_name('os'),
            'release': factory.make_name('release'),
            'arch': factory.make_name('arch'),
            'label': factory.make_name('label'),
        }

    def test_inserts_archive(self):
        product = self.make_product('archive.tar.xz')
        # Mock in test data
        repo_writer = download_resources.RepoWriter(None, None, None)
        self.patch(
            download_resources, 'products_exdata').return_value = product
        self.patch(repo_writer, 'product_mapping')
        mock_extract_archive_tar = self.patch(
            download_resources, 'extract_archive_tar')
        mock_link_resources = self.patch(download_resources, 'link_resources')
        repo_writer.insert_item(product, None, None, None, None)
        self.assertThat(
            mock_extract_archive_tar,
            MockCalledOnceWith(
                mock.ANY, os.path.basename(product['path']), product['sha256'],
                {'sha256': product['sha256']}, product['size'], None))
        self.assertThat(
            mock_link_resources,
            MockCalledOnceWith(
                snapshot_path=None, links=mock.ANY, osystem=product['os'],
                arch=product['arch'], release=product['release'],
                label=product['label'], subarches=mock.ANY))

    def test_inserts_root_image(self):
        product = self.make_product('root-image.gz')
        # Mock in test data
        repo_writer = download_resources.RepoWriter(None, None, None)
        self.patch(
            download_resources, 'products_exdata').return_value = product
        self.patch(repo_writer, 'product_mapping')
        mock_insert_root_image = self.patch(
            download_resources, 'insert_root_image')
        mock_link_resources = self.patch(download_resources, 'link_resources')
        repo_writer.insert_item(product, None, None, None, None)
        self.assertThat(
            mock_insert_root_image,
            MockCalledOnceWith(
                mock.ANY, product['sha256'], {'sha256': product['sha256']},
                product['size'], None))
        self.assertThat(
            mock_link_resources,
            MockCalledOnceWith(
                snapshot_path=None, links=mock.ANY, osystem=product['os'],
                arch=product['arch'], release=product['release'],
                label=product['label'], subarches=mock.ANY))

    def test_inserts_file(self):
        product = self.make_product()
        # Mock in test data
        repo_writer = download_resources.RepoWriter(None, None, None)
        self.patch(
            download_resources, 'products_exdata').return_value = product
        self.patch(repo_writer, 'product_mapping')
        mock_insert_file = self.patch(download_resources, 'insert_file')
        mock_link_resources = self.patch(download_resources, 'link_resources')
        repo_writer.insert_item(product, None, None, None, None)
        self.assertThat(
            mock_insert_file,
            MockCalledOnceWith(
                mock.ANY, os.path.basename(product['path']), product['sha256'],
                {'sha256': product['sha256']}, product['size'], None))
        self.assertThat(
            mock_link_resources,
            MockCalledOnceWith(
                snapshot_path=None, links=mock.ANY, osystem=product['os'],
                arch=product['arch'], release=product['release'],
                label=product['label'], subarches=mock.ANY))
