# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for bcache API."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

import httplib
import json
from uuid import uuid4

from django.core.urlresolvers import reverse
from maasserver.enum import (
    CACHE_MODE_TYPE,
    FILESYSTEM_GROUP_TYPE,
    FILESYSTEM_TYPE,
    NODE_STATUS,
)
from maasserver.testing.api import APITestCase
from maasserver.testing.factory import factory
from maasserver.testing.orm import reload_object
from maasserver.utils.converters import human_readable_bytes
from testtools.matchers import (
    ContainsDict,
    Equals,
)


def get_bcache_devices_uri(node):
    """Return a Node's bcache devices URI on the API."""
    return reverse(
        'bcache_devices_handler', args=[node.system_id])


def get_bcache_device_uri(bcache, node=None):
    """Return a bcache device URI on the API."""
    if node is None:
        node = bcache.get_node()
    return reverse(
        'bcache_device_handler', args=[node.system_id, bcache.id])


class TestBcacheDevicesAPI(APITestCase):

    def test_handler_path(self):
        node = factory.make_Node()
        self.assertEqual(
            '/api/1.0/nodes/%s/bcaches/' % (node.system_id),
            get_bcache_devices_uri(node))

    def test_read(self):
        node = factory.make_Node()
        bcaches = [
            factory.make_FilesystemGroup(
                node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE)
            for _ in range(3)
        ]
        # Not bcache. Should not be in the output.
        for _ in range(3):
            factory.make_FilesystemGroup(
                node=node, group_type=factory.pick_enum(
                    FILESYSTEM_GROUP_TYPE,
                    but_not=FILESYSTEM_GROUP_TYPE.BCACHE))
        uri = get_bcache_devices_uri(node)
        response = self.client.get(uri)

        self.assertEqual(httplib.OK, response.status_code, response.content)
        expected_ids = [
            bcache.id
            for bcache in bcaches
            ]
        result_ids = [
            bcache["id"]
            for bcache in json.loads(response.content)
            ]
        self.assertItemsEqual(expected_ids, result_ids)

    def test_create_403_if_not_admin(self):
        node = factory.make_Node(status=NODE_STATUS.READY)
        backing_size = 10 * 1000 ** 4
        cache_set = factory.make_CacheSet(node=node)
        backing_device = factory.make_PhysicalBlockDevice(
            node=node, size=backing_size)
        uuid = unicode(uuid4())
        uri = get_bcache_devices_uri(node)
        response = self.client.post(uri, {
            'name': 'bcache0',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEBACK,
            'cache_set': cache_set.id,
            'backing_device': backing_device.id,
        })
        self.assertEqual(
            httplib.FORBIDDEN, response.status_code, response.content)

    def test_create_409_if_not_ready(self):
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.ALLOCATED)
        backing_size = 10 * 1000 ** 4
        cache_set = factory.make_CacheSet(node=node)
        backing_device = factory.make_PhysicalBlockDevice(
            node=node, size=backing_size)
        uuid = unicode(uuid4())
        uri = get_bcache_devices_uri(node)
        response = self.client.post(uri, {
            'name': 'bcache0',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEBACK,
            'cache_set': cache_set.id,
            'backing_device': backing_device.id,
        })
        self.assertEqual(
            httplib.CONFLICT, response.status_code, response.content)

    def test_create(self):
        """Tests Bcache device creation."""
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        backing_size = 10 * 1000 ** 4
        cache_set = factory.make_CacheSet(node=node)
        backing_device = factory.make_PhysicalBlockDevice(
            node=node, size=backing_size)
        uuid = unicode(uuid4())
        uri = get_bcache_devices_uri(node)
        response = self.client.post(uri, {
            'name': 'bcache0',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEBACK,
            'cache_set': cache_set.id,
            'backing_device': backing_device.id,
        })
        self.assertEqual(httplib.OK, response.status_code, response.content)
        parsed_device = json.loads(response.content)
        self.assertEqual(backing_size, parsed_device['virtual_device']['size'])
        self.assertItemsEqual('bcache0', parsed_device['name'])
        self.assertItemsEqual(uuid, parsed_device['uuid'])

    def test_create_with_missing_cache_set_fails(self):
        """Tests Bcache device creation without a cache set."""
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        backing_size = 10 * 1000 ** 4
        backing_device = factory.make_PhysicalBlockDevice(
            node=node, size=backing_size)
        uuid = unicode(uuid4())
        uri = get_bcache_devices_uri(node)
        response = self.client.post(uri, {
            'name': 'bcache0',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEBACK,
            'backing_device': backing_device.id,
        })
        self.assertEqual(
            httplib.BAD_REQUEST, response.status_code, response.content)
        parsed_content = json.loads(response.content)
        self.assertIn(
            'Bcache requires a cache_set.',
            parsed_content['__all__'])
        self.assertIsNone(backing_device.filesystem)

    def test_create_with_missing_backing_fails(self):
        """Tests Bcache device creation without a backing device."""
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        cache_set = factory.make_CacheSet(node=node)
        uuid = unicode(uuid4())
        uri = get_bcache_devices_uri(node)
        response = self.client.post(uri, {
            'name': 'bcache0',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEBACK,
            'cache_set': cache_set.id,
        })
        self.assertEqual(
            httplib.BAD_REQUEST, response.status_code, response.content)
        parsed_content = json.loads(response.content)
        self.assertIn(
            'Either backing_device or backing_partition must be specified.',
            parsed_content['__all__'])


class TestBcacheDeviceAPI(APITestCase):

    def test_handler_path(self):
        node = factory.make_Node()
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE)
        self.assertEqual(
            '/api/1.0/nodes/%s/bcache/%s/' % (
                node.system_id, bcache.id),
            get_bcache_device_uri(bcache, node=node))

    def test_read(self):
        node = factory.make_Node()
        cache_set = factory.make_CacheSet(node=node)
        backing_block_device = factory.make_PhysicalBlockDevice(node=node)
        backing_filesystem = factory.make_Filesystem(
            fstype=FILESYSTEM_TYPE.BCACHE_BACKING,
            block_device=backing_block_device)
        bcache = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.BCACHE, cache_set=cache_set,
            filesystems=[backing_filesystem])
        uri = get_bcache_device_uri(bcache)
        response = self.client.get(uri)

        self.assertEqual(httplib.OK, response.status_code, response.content)
        parsed_bcache = json.loads(response.content)
        self.assertThat(parsed_bcache, ContainsDict({
            "id": Equals(bcache.id),
            "uuid": Equals(bcache.uuid),
            "name": Equals(bcache.name),
            "size": Equals(bcache.get_size()),
            "human_size": Equals(
                human_readable_bytes(bcache.get_size())),
            "resource_uri": Equals(get_bcache_device_uri(bcache)),
            "virtual_device": ContainsDict({
                "id": Equals(bcache.virtual_device.id),
                }),
            "cache_set": ContainsDict({
                "id": Equals(cache_set.id),
                "name": Equals(cache_set.name),
                }),
            "backing_device": ContainsDict({
                "id": Equals(backing_block_device.id),
                }),
            }))

    def test_read_404_when_not_bcache(self):
        not_bcache = factory.make_FilesystemGroup(
            group_type=factory.pick_enum(
                FILESYSTEM_GROUP_TYPE, but_not=FILESYSTEM_GROUP_TYPE.BCACHE))
        uri = get_bcache_device_uri(not_bcache)
        response = self.client.get(uri)
        self.assertEqual(
            httplib.NOT_FOUND, response.status_code, response.content)

    def test_delete_deletes_bcache(self):
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE)
        uri = get_bcache_device_uri(bcache)
        response = self.client.delete(uri)
        self.assertEqual(
            httplib.NO_CONTENT, response.status_code, response.content)
        self.assertIsNone(reload_object(bcache))

    def test_delete_403_when_not_admin(self):
        node = factory.make_Node(status=NODE_STATUS.READY)
        bcache = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.BCACHE, node=node)
        uri = get_bcache_device_uri(bcache)
        response = self.client.delete(uri)
        self.assertEqual(
            httplib.FORBIDDEN, response.status_code, response.content)

    def test_delete_404_when_not_bcache(self):
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        not_bcache = factory.make_FilesystemGroup(
            node=node, group_type=factory.pick_enum(
                FILESYSTEM_GROUP_TYPE, but_not=FILESYSTEM_GROUP_TYPE.BCACHE))
        uri = get_bcache_device_uri(not_bcache)
        response = self.client.delete(uri)
        self.assertEqual(
            httplib.NOT_FOUND, response.status_code, response.content)

    def test_delete_409_when_not_ready(self):
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.ALLOCATED)
        bcache = factory.make_FilesystemGroup(
            group_type=FILESYSTEM_GROUP_TYPE.BCACHE, node=node)
        uri = get_bcache_device_uri(bcache)
        response = self.client.delete(uri)
        self.assertEqual(
            httplib.CONFLICT, response.status_code, response.content)

    def test_update_bcache(self):
        """Tests update bcache method by changing the name, UUID and cache
        mode of a bcache."""
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
            cache_mode=CACHE_MODE_TYPE.WRITEBACK)
        uri = get_bcache_device_uri(bcache)
        uuid = unicode(uuid4())
        filesystem_ids = [fs.id for fs in bcache.filesystems.all()]
        response = self.client.put(uri, {
            'name': 'new_name',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEAROUND,
        })
        self.assertEqual(httplib.OK, response.status_code, response.content)
        parsed_device = json.loads(response.content)
        self.assertEqual('new_name', parsed_device['name'])
        self.assertEqual(uuid, parsed_device['uuid'])
        self.assertEqual(
            CACHE_MODE_TYPE.WRITEAROUND, parsed_device['cache_mode'])
        # Ensure the filesystems were not changed.
        self.assertListEqual(
            filesystem_ids, [fs.id for fs in bcache.filesystems.all()])

    def test_change_bcache_backing(self):
        """Tests update bcache method by changing backing device to different
        block device."""
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
            cache_mode=CACHE_MODE_TYPE.WRITEBACK)
        uri = get_bcache_device_uri(bcache)
        new_backing = factory.make_PhysicalBlockDevice(node=node)
        response = self.client.put(uri, {
            'backing_device': new_backing.id
        })
        self.assertEqual(httplib.OK, response.status_code, response.content)
        parsed_device = json.loads(response.content)
        self.assertEqual(new_backing.id, parsed_device['backing_device']['id'])
        self.assertEqual('physical', parsed_device['backing_device']['type'])

    def test_change_storages_to_partitions_bcache(self):
        """Tests update bcache method by changing backing device to a
        partition."""
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
            cache_mode=CACHE_MODE_TYPE.WRITEBACK)
        uri = get_bcache_device_uri(bcache)
        new_backing = factory.make_PartitionTable(
            block_device=factory.make_PhysicalBlockDevice(
                node=node)).add_partition()
        response = self.client.put(uri, {
            'backing_partition': new_backing.id
        })
        self.assertEqual(httplib.OK, response.status_code, response.content)
        parsed_device = json.loads(response.content)
        self.assertEqual(new_backing.id, parsed_device['backing_device']['id'])
        self.assertEqual('partition', parsed_device['backing_device']['type'])

    def test_invalid_change_fails(self):
        """Tests changing the backing of a bcache device to None fails."""
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.READY)
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
            cache_mode=CACHE_MODE_TYPE.WRITEBACK)
        uri = get_bcache_device_uri(bcache)
        new_backing = factory.make_PhysicalBlockDevice()  # On other node.
        response = self.client.put(uri, {
            'backing_device': new_backing.id,
        })
        self.assertEqual(
            httplib.BAD_REQUEST, response.status_code, response.content)
        parsed_content = json.loads(response.content)
        self.assertIn(
            'Select a valid choice.',
            parsed_content['backing_device'][0])

    def test_update_403_if_not_admin(self):
        node = factory.make_Node(status=NODE_STATUS.READY)
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
            cache_mode=CACHE_MODE_TYPE.WRITEBACK)
        uri = get_bcache_device_uri(bcache)
        uuid = unicode(uuid4())
        response = self.client.put(uri, {
            'name': 'new_name',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEAROUND,
        })
        self.assertEqual(
            httplib.FORBIDDEN, response.status_code, response.content)

    def test_update_409_if_not_ready(self):
        self.become_admin()
        node = factory.make_Node(status=NODE_STATUS.ALLOCATED)
        bcache = factory.make_FilesystemGroup(
            node=node, group_type=FILESYSTEM_GROUP_TYPE.BCACHE,
            cache_mode=CACHE_MODE_TYPE.WRITEBACK)
        uri = get_bcache_device_uri(bcache)
        uuid = unicode(uuid4())
        response = self.client.put(uri, {
            'name': 'new_name',
            'uuid': uuid,
            'cache_mode': CACHE_MODE_TYPE.WRITEAROUND,
        })
        self.assertEqual(
            httplib.CONFLICT, response.status_code, response.content)
