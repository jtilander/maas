# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test the storage layouts."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
)

str = None

__metaclass__ = type
__all__ = []

from math import ceil
import random

from maasserver.enum import (
    CACHE_MODE_TYPE,
    FILESYSTEM_GROUP_TYPE,
    FILESYSTEM_TYPE,
    PARTITION_TABLE_TYPE,
)
from maasserver.models.blockdevice import MIN_BLOCK_DEVICE_SIZE
from maasserver.models.filesystemgroup import VolumeGroup
from maasserver.models.partition import MAX_PARTITION_SIZE_FOR_MBR
from maasserver.models.partitiontable import PARTITION_TABLE_EXTRA_SPACE
from maasserver.storage_layouts import (
    BcacheStorageLayout,
    BcacheStorageLayoutBase,
    calculate_size_from_precentage,
    EFI_PARTITION_SIZE,
    FlatStorageLayout,
    get_storage_layout_choices,
    get_storage_layout_for_node,
    is_precentage,
    LVMStorageLayout,
    MIN_BOOT_PARTITION_SIZE,
    MIN_ROOT_PARTITION_SIZE,
    StorageLayoutBase,
    StorageLayoutFieldsError,
    StorageLayoutForm,
    StorageLayoutMissingBootDiskError,
)
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maastesting.matchers import MockCalledOnceWith
from testtools.matchers import MatchesStructure


LARGE_BLOCK_DEVICE = 10 * 1024 * 1024 * 1024  # 10 GiB


def round_size_by_blocks(size, block_size):
    number_of_blocks = size / block_size
    if size % block_size > 0:
        number_of_blocks += 1
    return number_of_blocks * block_size


def make_Node_with_uefi_boot_method(*args, **kwargs):
    kwargs['bios_boot_method'] = "uefi"
    kwargs['with_boot_disk'] = False
    return factory.make_Node(*args, **kwargs)


class TestFormHelpers(MAASServerTestCase):

    def test_get_storage_layout_choices(self):
        self.assertItemsEqual([
            ("flat", "Flat layout"),
            ("lvm", "LVM layout"),
            ("bcache", "Bcache layout"),
            ], get_storage_layout_choices())

    def test_get_storage_layout_for_node(self):
        node = make_Node_with_uefi_boot_method()
        layout = get_storage_layout_for_node("flat", node)
        self.assertIsInstance(layout, FlatStorageLayout)
        self.assertEquals(node, layout.node)


class TestStorageLayoutForm(MAASServerTestCase):

    def test__field_is_not_required(self):
        form = StorageLayoutForm(required=False, data={})
        self.assertTrue(form.is_valid(), form.errors)

    def test__field_is_required(self):
        form = StorageLayoutForm(required=True, data={})
        self.assertFalse(form.is_valid(), form.errors)
        self.assertEquals({
            'storage_layout': ['This field is required.'],
            }, form.errors)


class TestIsPrecentageHelper(MAASServerTestCase):
    """Tests for `is_precentage`."""

    scenarios = [
        ('100%', {
            'value': '100%',
            'is_precentage': True,
            }),
        ('10%', {
            'value': '10%',
            'is_precentage': True,
            }),
        ('1.5%', {
            'value': '1.5%',
            'is_precentage': True,
            }),
        ('1000.42%', {
            'value': '1000.42%',
            'is_precentage': True,
            }),
        ('0.816112383915%', {
            'value': '0.816112383915%',
            'is_precentage': True,
            }),
        ('1000', {
            'value': '1000',
            'is_precentage': False,
            }),
        ('10', {
            'value': '10',
            'is_precentage': False,
            }),
        ('0', {
            'value': '0',
            'is_precentage': False,
            }),
        ('int(0)', {
            'value': 0,
            'is_precentage': False,
            }),
    ]

    def test__returns_correct_result(self):
        self.assertEquals(
            self.is_precentage, is_precentage(self.value),
            "%s gave incorrect result." % self.value)


class TestCalculateSizeFromPrecentHelper(MAASServerTestCase):
    """Tests for `calculate_size_from_precentage`."""

    scenarios = [
        ('100%', {
            'input': 10000,
            'precent': '100%',
            'output': 10000,
            }),
        ('10%', {
            'input': 10000,
            'precent': '10%',
            'output': 1000,
            }),
        ('1%', {
            'input': 10000,
            'precent': '1%',
            'output': 100,
            }),
        ('5%', {
            'input': 4096,
            'precent': '5%',
            'output': int(ceil(4096 * .05)),
            }),
        ('0.816112383915%', {
            'input': 4096,
            'precent': '0.816112383915%',
            'output': int(ceil(4096 * 0.00816112383915)),
            }),
    ]

    def test__returns_correct_result(self):
        self.assertEquals(
            self.output,
            calculate_size_from_precentage(self.input, self.precent),
            "%s gave incorrect result." % self.precent)


class TestStorageLayoutBase(MAASServerTestCase):
    """Tests for `StorageLayoutBase`."""

    def test__init__sets_node(self):
        node = make_Node_with_uefi_boot_method()
        layout = StorageLayoutBase(node)
        self.assertEquals(node, layout.node)

    def test__init__loads_the_physical_block_devices(self):
        node = make_Node_with_uefi_boot_method()
        block_devices = [
            factory.make_PhysicalBlockDevice(node=node)
            for _ in range(3)
        ]
        layout = StorageLayoutBase(node)
        self.assertEquals(block_devices, layout.block_devices)

    def test_raises_error_when_no_block_devices(self):
        node = make_Node_with_uefi_boot_method()
        layout = StorageLayoutBase(node)
        error = self.assertRaises(
            StorageLayoutMissingBootDiskError, layout.configure)
        self.assertEquals(
            "Node doesn't have any storage devices to configure.",
            error.message)

    def test_raises_error_when_precentage_to_low_for_boot_disk(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'boot_size': "0%",
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "boot_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_BOOT_PARTITION_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_value_to_low_for_boot_disk(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'boot_size': MIN_BOOT_PARTITION_SIZE - 1,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "boot_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_BOOT_PARTITION_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_precentage_to_high_for_boot_disk(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        max_size = (
            boot_disk.size - EFI_PARTITION_SIZE - MIN_ROOT_PARTITION_SIZE)
        to_high_precent = max_size / float(boot_disk.size)
        to_high_precent = "%s%%" % ((to_high_precent + 1) * 100)
        layout = StorageLayoutBase(node, {
            'boot_size': to_high_precent,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "boot_size": [
                "Size is too large. Maximum size is %s." % max_size],
            }, error.error_dict)

    def test_raises_error_when_value_to_high_for_boot_disk(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        max_size = (
            boot_disk.size - EFI_PARTITION_SIZE - MIN_ROOT_PARTITION_SIZE)
        layout = StorageLayoutBase(node, {
            'boot_size': max_size + 1,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "boot_size": [
                "Size is too large. Maximum size is %s." % max_size],
            }, error.error_dict)

    def test_raises_error_when_precentage_to_low_for_root_disk(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'root_size': "0%",
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "root_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_ROOT_PARTITION_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_value_to_low_for_root_disk(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'root_size': MIN_ROOT_PARTITION_SIZE - 1,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "root_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_ROOT_PARTITION_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_precentage_to_high_for_root_disk(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        max_size = (
            boot_disk.size - EFI_PARTITION_SIZE - MIN_BOOT_PARTITION_SIZE)
        to_high_precent = max_size / float(boot_disk.size)
        to_high_precent = "%s%%" % ((to_high_precent + 1) * 100)
        layout = StorageLayoutBase(node, {
            'root_size': to_high_precent,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "root_size": [
                "Size is too large. Maximum size is %s." % max_size],
            }, error.error_dict)

    def test_raises_error_when_value_to_high_for_root_disk(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        max_size = (
            boot_disk.size - EFI_PARTITION_SIZE - MIN_BOOT_PARTITION_SIZE)
        layout = StorageLayoutBase(node, {
            'root_size': max_size + 1,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "root_size": [
                "Size is too large. Maximum size is %s." % max_size],
            }, error.error_dict)

    def test_raises_error_when_boot_and_root_to_big(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'boot_size': "50%",
            'root_size': "60%",
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "__all__": [
                "Size of the boot partition and root partition are larger "
                "than the available space on the boot disk."],
            }, error.error_dict)

    def test_doesnt_error_if_boot_and_root_valid(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'boot_size': "50%",
            'root_size': "50%",
            })
        self.patch(StorageLayoutBase, "configure_storage")
        # This should not raise an exception.
        layout.configure()

    def test_get_boot_size_returns_0_if_not_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'root_size': "50%",
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(0, layout.get_boot_size())

    def test_get_boot_size_returns_boot_size_if_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        boot_size = random.randint(
            MIN_BOOT_PARTITION_SIZE, MIN_BOOT_PARTITION_SIZE * 2)
        layout = StorageLayoutBase(node, {
            'boot_size': boot_size,
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(boot_size, layout.get_boot_size())

    def test_get_root_device_returns_None_if_not_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertIsNone(layout.get_root_device())

    def test_get_root_device_returns_root_device_if_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        root_device = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            'root_device': root_device.id,
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(root_device, layout.get_root_device())

    def test_get_root_size_returns_None_if_not_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node, {
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertIsNone(layout.get_root_size())

    def test_get_root_size_returns_root_size_if_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        root_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = StorageLayoutBase(node, {
            'root_size': root_size,
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(root_size, layout.get_root_size())

    def test_configure_calls_configure_storage(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = StorageLayoutBase(node)
        mock_configure_storage = self.patch(
            StorageLayoutBase, "configure_storage")
        layout.configure()
        self.assertThat(mock_configure_storage, MockCalledOnceWith(True))


class LayoutHelpersMixin:

    def assertEFIPartition(self, partition, boot_disk):
        self.assertIsNotNone(partition)
        self.assertEquals(
            round_size_by_blocks(EFI_PARTITION_SIZE, boot_disk.block_size),
            partition.size)
        self.assertThat(
            partition.get_effective_filesystem(), MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.FAT32,
                label="efi",
                mount_point="/boot/efi",
                ))


class TestFlatStorageLayout(MAASServerTestCase, LayoutHelpersMixin):

    def test__init_sets_up_all_fields(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = FlatStorageLayout(node)
        self.assertItemsEqual([
            'root_device',
            'root_size',
            'boot_size',
            ], layout.fields.keys())

    def test__creates_layout_with_mbr_defaults(self):
        node = factory.make_Node(with_boot_disk=False)
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = FlatStorageLayout(node)
        layout.configure()

        # Validate partition table.
        partition_table = boot_disk.get_partitiontable()
        self.assertEquals(PARTITION_TABLE_TYPE.MBR, partition_table.table_type)

        # Validate root partition.
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[0]
        self.assertIsNotNone(root_partition)
        self.assertEquals(
            round_size_by_blocks(
                boot_disk.size - PARTITION_TABLE_EXTRA_SPACE,
                boot_disk.block_size),
            root_partition.size)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_maximum_mbr_partition_size(self):
        node = factory.make_Node(with_boot_disk=False)
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=3 * (1024 ** 4))
        layout = FlatStorageLayout(node)
        layout.configure()

        # Validate partition table.
        partition_table = boot_disk.get_partitiontable()
        self.assertEquals(PARTITION_TABLE_TYPE.MBR, partition_table.table_type)

        # Validate root partition.
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[0]
        number_of_blocks = MAX_PARTITION_SIZE_FOR_MBR / boot_disk.block_size
        self.assertIsNotNone(root_partition)
        self.assertEquals(
            (boot_disk.block_size * (number_of_blocks - 1)),
            root_partition.size)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_uefi_defaults(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = FlatStorageLayout(node)
        layout.configure()

        # Validate partition table.
        partition_table = boot_disk.get_partitiontable()
        self.assertEquals(PARTITION_TABLE_TYPE.GPT, partition_table.table_type)

        # Validate efi partition.
        partitions = partition_table.partitions.order_by('id').all()
        efi_partition = partitions[0]
        self.assertEFIPartition(efi_partition, boot_disk)

        # Validate root partition.
        root_partition = partitions[1]
        self.assertIsNotNone(root_partition)
        self.assertEquals(
            round_size_by_blocks(
                boot_disk.size - EFI_PARTITION_SIZE -
                PARTITION_TABLE_EXTRA_SPACE,
                boot_disk.block_size),
            root_partition.size)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_boot_size(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        boot_size = random.randint(
            MIN_BOOT_PARTITION_SIZE, MIN_BOOT_PARTITION_SIZE * 2)
        layout = FlatStorageLayout(node, {
            'boot_size': boot_size,
            })
        layout.configure()

        # Validate partition table.
        partition_table = boot_disk.get_partitiontable()
        self.assertEquals(PARTITION_TABLE_TYPE.GPT, partition_table.table_type)

        # Validate efi partition.
        partitions = partition_table.partitions.order_by('id').all()
        efi_partition = partitions[0]
        self.assertEFIPartition(efi_partition, boot_disk)

        # Validate boot partition.
        boot_partition = partitions[1]
        self.assertIsNotNone(boot_partition)
        self.assertEquals(
            round_size_by_blocks(
                boot_size, boot_disk.block_size),
            boot_partition.size)
        self.assertThat(
            boot_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="boot",
                mount_point="/boot",
                ))

        # Validate root partition.
        root_partition = partitions[2]
        self.assertIsNotNone(root_partition)
        self.assertEquals(
            round_size_by_blocks(
                boot_disk.size - boot_partition.size -
                EFI_PARTITION_SIZE - PARTITION_TABLE_EXTRA_SPACE,
                boot_disk.block_size),
            root_partition.size)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_root_size(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        root_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = FlatStorageLayout(node, {
            'root_size': root_size,
            })
        layout.configure()

        # Validate partition table.
        partition_table = boot_disk.get_partitiontable()
        self.assertEquals(PARTITION_TABLE_TYPE.GPT, partition_table.table_type)

        # Validate efi partition.
        partitions = partition_table.partitions.order_by('id').all()
        efi_partition = partitions[0]
        self.assertEFIPartition(efi_partition, boot_disk)

        # Validate root partition.
        root_partition = partitions[1]
        self.assertIsNotNone(root_partition)
        self.assertEquals(
            round_size_by_blocks(root_size, boot_disk.block_size),
            root_partition.size)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_boot_size_and_root_size(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        boot_size = random.randint(
            MIN_BOOT_PARTITION_SIZE, MIN_BOOT_PARTITION_SIZE * 2)
        root_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = FlatStorageLayout(node, {
            'boot_size': boot_size,
            'root_size': root_size,
            })
        layout.configure()

        # Validate partition table.
        partition_table = boot_disk.get_partitiontable()
        self.assertEquals(PARTITION_TABLE_TYPE.GPT, partition_table.table_type)

        # Validate efi partition.
        partitions = partition_table.partitions.order_by('id').all()
        efi_partition = partitions[0]
        self.assertEFIPartition(efi_partition, boot_disk)

        # Validate boot partition.
        boot_partition = partitions[1]
        self.assertIsNotNone(boot_partition)
        self.assertEquals(
            round_size_by_blocks(
                boot_size, boot_disk.block_size),
            boot_partition.size)
        self.assertThat(
            boot_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="boot",
                mount_point="/boot",
                ))

        # Validate root partition.
        root_partition = partitions[2]
        self.assertIsNotNone(root_partition)
        self.assertEquals(
            round_size_by_blocks(root_size, boot_disk.block_size),
            root_partition.size)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_root_device_and_root_size(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        root_device = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        root_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = FlatStorageLayout(node, {
            'root_device': root_device.id,
            'root_size': root_size,
            })
        layout.configure()

        # Validate boot partition table.
        boot_partition_table = boot_disk.get_partitiontable()
        self.assertEquals(
            PARTITION_TABLE_TYPE.GPT, boot_partition_table.table_type)

        # Validate efi partition.
        boot_partitions = boot_partition_table.partitions.order_by('id').all()
        efi_partition = boot_partitions[0]
        self.assertEFIPartition(efi_partition, boot_disk)

        # Validate the root device partition table and partition.
        root_partition_table = root_device.get_partitiontable()
        self.assertEquals(
            PARTITION_TABLE_TYPE.GPT, boot_partition_table.table_type)
        root_partition = root_partition_table.partitions.order_by(
            'id').all()[0]
        self.assertIsNotNone(root_partition)
        self.assertEquals(
            round_size_by_blocks(root_size, root_device.block_size),
            root_partition.size)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))


class TestLVMStorageLayout(MAASServerTestCase, LayoutHelpersMixin):

    def test__init_sets_up_all_fields(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node)
        self.assertItemsEqual([
            'root_device',
            'root_size',
            'boot_size',
            'vg_name',
            'lv_name',
            'lv_size',
            ], layout.fields.keys())

    def test_get_vg_name_returns_default_if_not_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node, {
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(layout.DEFAULT_VG_NAME, layout.get_vg_name())

    def test_get_vg_name_returns_vg_name_if_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        vg_name = factory.make_name("vg")
        layout = LVMStorageLayout(node, {
            'vg_name': vg_name,
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(vg_name, layout.get_vg_name())

    def test_get_lv_name_returns_default_if_not_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node, {
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(layout.DEFAULT_LV_NAME, layout.get_lv_name())

    def test_get_lv_name_returns_lv_name_if_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        lv_name = factory.make_name("lv")
        layout = LVMStorageLayout(node, {
            'lv_name': lv_name,
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(lv_name, layout.get_lv_name())

    def test_get_lv_size_returns_None_if_not_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node, {
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertIsNone(layout.get_lv_size())

    def test_get_lv_size_returns_lv_size_if_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        lv_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = LVMStorageLayout(node, {
            'lv_size': lv_size,
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        self.assertEquals(lv_size, layout.get_lv_size())

    def test_get_calculated_lv_size_returns_set_lv_size(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        lv_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = LVMStorageLayout(node, {
            'lv_size': lv_size,
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        volume_group = factory.make_VolumeGroup(node=node)
        self.assertEquals(lv_size, layout.get_calculated_lv_size(volume_group))

    def test_get_calculated_lv_size_returns_size_of_volume_group(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node, {
            })
        self.assertTrue(layout.is_valid(), layout.errors)
        volume_group = factory.make_VolumeGroup(node=node)
        self.assertEquals(
            volume_group.get_size(),
            layout.get_calculated_lv_size(volume_group))

    def test_raises_error_when_precentage_to_low_for_logical_volume(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node, {
            'lv_size': "0%",
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "lv_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_ROOT_PARTITION_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_value_to_low_for_logical_volume(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node, {
            'lv_size': MIN_ROOT_PARTITION_SIZE - 1,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "lv_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_ROOT_PARTITION_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_precentage_to_high_for_logical_volume(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        root_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = LVMStorageLayout(node, {
            'root_size': root_size,
            'lv_size': "101%",
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "lv_size": [
                "Size is too large. Maximum size is %s." % root_size],
            }, error.error_dict)

    def test_raises_error_when_value_to_high_for_logical_volume(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        max_size = (
            boot_disk.size - EFI_PARTITION_SIZE)
        layout = LVMStorageLayout(node, {
            'lv_size': max_size + 1,
            })
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "lv_size": [
                "Size is too large. Maximum size is %s." % max_size],
            }, error.error_dict)

    def test__creates_layout_with_defaults(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = LVMStorageLayout(node)
        layout.configure()

        # Validate the volume group on root partition.
        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[1]
        volume_group = VolumeGroup.objects.get(
            filesystems__partition=root_partition)
        self.assertIsNotNone(volume_group)
        self.assertEquals(layout.DEFAULT_VG_NAME, volume_group.name)

        # Validate one logical volume on volume group.
        self.assertEquals(
            1, volume_group.virtual_devices.count(),
            "Should have only 1 logical volume.")
        logical_volume = volume_group.virtual_devices.first()
        self.assertEquals(volume_group.get_size(), logical_volume.size)
        self.assertEquals(layout.DEFAULT_LV_NAME, logical_volume.name)
        self.assertThat(
            logical_volume.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_vg_name_and_lv_name(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        vg_name = factory.make_name("vg")
        lv_name = factory.make_name("lv")
        layout = LVMStorageLayout(node, {
            "vg_name": vg_name,
            "lv_name": lv_name,
            })
        layout.configure()

        # Validate the volume group on root partition.
        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[1]
        volume_group = VolumeGroup.objects.get(
            filesystems__partition=root_partition)
        self.assertIsNotNone(volume_group)
        self.assertEquals(vg_name, volume_group.name)

        # Validate one logical volume on volume group.
        self.assertEquals(
            1, volume_group.virtual_devices.count(),
            "Should have only 1 logical volume.")
        logical_volume = volume_group.virtual_devices.first()
        self.assertEquals(volume_group.get_size(), logical_volume.size)
        self.assertEquals(lv_name, logical_volume.name)
        self.assertThat(
            logical_volume.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_lv_size(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        lv_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout = LVMStorageLayout(node, {
            "lv_size": lv_size,
            })
        layout.configure()

        # Validate the volume group on root partition.
        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[1]
        volume_group = VolumeGroup.objects.get(
            filesystems__partition=root_partition)
        self.assertIsNotNone(volume_group)
        self.assertEquals(layout.DEFAULT_VG_NAME, volume_group.name)

        # Validate one logical volume on volume group.
        self.assertEquals(
            1, volume_group.virtual_devices.count(),
            "Should have only 1 logical volume.")
        logical_volume = volume_group.virtual_devices.first()
        self.assertEquals(lv_size, logical_volume.size)
        self.assertEquals(layout.DEFAULT_LV_NAME, logical_volume.name)
        self.assertThat(
            logical_volume.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test__creates_layout_with_multiple_mbr_partitions(self):
        node = factory.make_Node(with_boot_disk=False)
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=7 * (1024 ** 4))
        layout = LVMStorageLayout(node)
        layout.configure()

        # Validate the volume group on root partition.
        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[0]
        volume_group = VolumeGroup.objects.get(
            filesystems__partition=root_partition)
        number_of_blocks = MAX_PARTITION_SIZE_FOR_MBR / boot_disk.block_size
        self.assertIsNotNone(volume_group)
        self.assertEquals(
            4, partition_table.partitions.count(),
            "Should have 4 partitions.")
        self.assertEquals(
            boot_disk.block_size * (number_of_blocks - 1),
            root_partition.size)


class TestBcacheStorageLayoutBase(MAASServerTestCase):

    def test_setup_cache_device_field_does_nothing_if_no_boot_device(self):
        node = make_Node_with_uefi_boot_method()
        layout = BcacheStorageLayoutBase(node)
        layout.setup_cache_device_field()
        self.assertNotIn('cache_device', layout.fields.keys())

    def test_setup_cache_device_field_doesnt_include_boot_device(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        other_disks = [
            factory.make_PhysicalBlockDevice(
                node=node, size=LARGE_BLOCK_DEVICE)
            for _ in range(3)
        ]
        valid_choices = [
            (disk.id, disk.id)
            for disk in other_disks
        ]
        layout = BcacheStorageLayoutBase(node)
        layout.setup_cache_device_field()
        self.assertItemsEqual(
            valid_choices, layout.fields['cache_device'].choices)

    def test__find_best_cache_device_returns_None_if_not_boot_disk(self):
        node = make_Node_with_uefi_boot_method()
        layout = BcacheStorageLayoutBase(node)
        self.assertIsNone(layout._find_best_cache_device())

    def test__find_best_cache_device_returns_smallest_ssd_first(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        # Small SSD
        factory.make_PhysicalBlockDevice(
            node=node, size=5 * 1024 * 1024 * 1024, tags=['ssd'])
        # Smallest SSD
        smallest_ssd = factory.make_PhysicalBlockDevice(
            node=node, size=2 * 1024 * 1024 * 1024, tags=['ssd'])
        # Very small not SSD
        factory.make_PhysicalBlockDevice(
            node=node, size=1 * 1024 * 1024 * 1024, tags=['rotary'])
        layout = BcacheStorageLayoutBase(node)
        self.assertEquals(smallest_ssd, layout._find_best_cache_device())

    def test__find_best_cache_device_returns_None_if_no_ssd(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        # Small Rotary
        factory.make_PhysicalBlockDevice(
            node=node, size=5 * 1024 * 1024 * 1024, tags=['rotary'])
        # Smallest Rotary
        factory.make_PhysicalBlockDevice(
            node=node, size=2 * 1024 * 1024 * 1024, tags=['rotary'])
        layout = BcacheStorageLayoutBase(node)
        self.assertIsNone(layout._find_best_cache_device())

    def test_get_cache_device_returns_set_cache_device_over_find(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        # Small SSD
        small_ssd = factory.make_PhysicalBlockDevice(
            node=node, size=5 * 1024 * 1024 * 1024, tags=['ssd'])
        # Smallest SSD
        factory.make_PhysicalBlockDevice(
            node=node, size=2 * 1024 * 1024 * 1024, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node)
        layout.cleaned_data = {
            'cache_device': small_ssd.id,
        }
        self.assertEquals(small_ssd, layout.get_cache_device())

    def test_get_cache_device_returns_the_best_cache_device_if_not_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        # Small SSD
        factory.make_PhysicalBlockDevice(
            node=node, size=5 * 1024 * 1024 * 1024, tags=['ssd'])
        # Smallest SSD
        smallest_ssd = factory.make_PhysicalBlockDevice(
            node=node, size=2 * 1024 * 1024 * 1024, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node)
        layout.cleaned_data = {}
        self.assertEquals(smallest_ssd, layout.get_cache_device())

    def test_get_cache_mode_returns_set_cache_mode(self):
        node = make_Node_with_uefi_boot_method()
        layout = BcacheStorageLayoutBase(node)
        cache_mode = factory.pick_enum(CACHE_MODE_TYPE)
        layout.cleaned_data = {
            'cache_mode': cache_mode,
        }
        self.assertEquals(cache_mode, layout.get_cache_mode())

    def test_get_cache_mode_returns_default_if_blank(self):
        node = make_Node_with_uefi_boot_method()
        layout = BcacheStorageLayoutBase(node)
        layout.cleaned_data = {
            'cache_mode': '',
        }
        self.assertEquals(layout.DEFAULT_CACHE_MODE, layout.get_cache_mode())

    def test_get_cache_size_returns_set_cache_size(self):
        node = make_Node_with_uefi_boot_method()
        layout = BcacheStorageLayoutBase(node)
        cache_size = random.randint(
            MIN_ROOT_PARTITION_SIZE, MIN_ROOT_PARTITION_SIZE * 2)
        layout.cleaned_data = {
            'cache_size': cache_size,
        }
        self.assertEquals(cache_size, layout.get_cache_size())

    def test_get_cache_size_returns_None_if_blank(self):
        node = make_Node_with_uefi_boot_method()
        layout = BcacheStorageLayoutBase(node)
        layout.cleaned_data = {
            'cache_size': '',
        }
        self.assertIsNone(layout.get_cache_size())

    def test_get_cache_no_part_returns_boolean(self):
        node = make_Node_with_uefi_boot_method()
        layout = BcacheStorageLayoutBase(node)
        cache_no_part = factory.pick_bool()
        layout.cleaned_data = {
            'cache_no_part': cache_no_part,
        }
        self.assertEquals(cache_no_part, layout.get_cache_no_part())

    def test_create_cache_set_setups_up_cache_device_with_partition(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=5 * 1024 * 1024 * 1024, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node)
        layout.cleaned_data = {
            'cache_no_part': False,
        }
        cache_set = layout.create_cache_set()
        cache_device = cache_set.get_device()
        partition_table = ssd.get_partitiontable()
        self.assertIsNotNone(partition_table)
        partition = partition_table.partitions.order_by('id').all()[0]
        self.assertEquals(partition, cache_device)

    def test_create_cache_set_setups_up_cache_device_without_part(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=5 * 1024 * 1024 * 1024, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node)
        layout.cleaned_data = {
            'cache_no_part': True,
        }
        cache_set = layout.create_cache_set()
        cache_device = cache_set.get_device()
        self.assertEquals(ssd, cache_device)

    def test_create_cache_set_setups_up_cache_device_with_cache_size(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=5 * 1024 * 1024 * 1024, block_size=4096,
            tags=['ssd'])
        cache_size = round_size_by_blocks(
            random.randint(
                3 * 1024 * 1024 * 1024, 5 * 1024 * 1024 * 1024),
            4096)
        layout = BcacheStorageLayoutBase(node)
        layout.cleaned_data = {
            'cache_size': cache_size,
            'cache_no_part': False,
        }
        cache_set = layout.create_cache_set()
        cache_device = cache_set.get_device()
        partition_table = ssd.get_partitiontable()
        self.assertIsNotNone(partition_table)
        partition = partition_table.partitions.order_by('id').all()[0]
        self.assertEquals(partition, cache_device)
        self.assertEquals(cache_size, partition.size)

    def test_raises_error_when_invalid_cache_device(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node, {
            "cache_device": boot_disk.id,
            })
        layout.setup_cache_device_field()
        self.assertFalse(layout.is_valid(), layout.errors)
        self.assertEquals({
            "cache_device": [
                "'%s' is not a valid cache_device.  It should be one "
                "of: '%s'." % (boot_disk.id, ssd.id)],
            }, layout.errors)

    def test_raises_error_when_cache_size_and_cache_no_part_set(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = BcacheStorageLayoutBase(node, {
            "cache_size": MIN_ROOT_PARTITION_SIZE,
            "cache_no_part": True,
            })
        layout.setup_cache_device_field()
        self.assertFalse(layout.is_valid(), layout.errors)
        self.assertEquals({
            "cache_size": [
                "Cannot use cache_size and cache_no_part at the same time."],
            "cache_no_part": [
                "Cannot use cache_size and cache_no_part at the same time."],
            }, layout.errors)

    def test_raises_error_when_precentage_to_low_for_cache_size(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node, {
            'cache_size': "0%",
            })
        layout.setup_cache_device_field()
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "cache_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_BLOCK_DEVICE_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_value_to_low_for_cache_size(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node, {
            'cache_size': MIN_BLOCK_DEVICE_SIZE - 1,
            })
        layout.setup_cache_device_field()
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "cache_size": [
                "Size is too small. Minimum size is %s." % (
                    MIN_BLOCK_DEVICE_SIZE)],
            }, error.error_dict)

    def test_raises_error_when_precentage_to_high_for_cache_size(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node, {
            'cache_size': "101%",
            })
        layout.setup_cache_device_field()
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "cache_size": [
                "Size is too large. Maximum size is %s." % ssd.size],
            }, error.error_dict)

    def test_raises_error_when_value_to_high_for_cache_size(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayoutBase(node, {
            'cache_size': ssd.size + 1,
            })
        layout.setup_cache_device_field()
        error = self.assertRaises(StorageLayoutFieldsError, layout.configure)
        self.assertEquals({
            "cache_size": [
                "Size is too large. Maximum size is %s." % ssd.size],
            }, error.error_dict)


class TestBcacheStorageLayout(MAASServerTestCase):

    def test__init_sets_up_cache_device_field(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = BcacheStorageLayout(node)
        self.assertIn('cache_device', layout.fields.keys())

    def test__init_sets_up_all_fields(self):
        node = make_Node_with_uefi_boot_method()
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = BcacheStorageLayout(node)
        self.assertItemsEqual([
            'root_device',
            'root_size',
            'boot_size',
            'cache_device',
            'cache_mode',
            'cache_size',
            'cache_no_part',
            ], layout.fields.keys())

    def test_configure_storage_creates_flat_layout_if_no_cache_device(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        layout = BcacheStorageLayout(node)
        layout.configure()

        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[1]
        self.assertIsNotNone(root_partition)
        self.assertThat(
            root_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test_configure_creates_boot_partition(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayout(node)
        layout.configure()

        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        boot_partition = partitions[1]
        self.assertEquals(1 * 1024 ** 3, boot_partition.size)
        self.assertThat(
            boot_partition.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="boot",
                mount_point="/boot"))

    def test_configure_storage_creates_bcache_layout_with_ssd(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayout(node)
        layout.configure()

        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[2]
        cache_partition_table = ssd.get_partitiontable()
        cache_partition = cache_partition_table.partitions.order_by(
            'id').all()[0]
        self.assertEquals(
            FILESYSTEM_TYPE.BCACHE_BACKING,
            root_partition.get_effective_filesystem().fstype)
        self.assertEquals(
            FILESYSTEM_TYPE.BCACHE_CACHE,
            cache_partition.get_effective_filesystem().fstype)
        root_filesystem = root_partition.get_effective_filesystem()
        self.assertEquals(
            FILESYSTEM_GROUP_TYPE.BCACHE,
            root_filesystem.filesystem_group.group_type)
        cache_filesystem = cache_partition.get_effective_filesystem()
        self.assertEquals(
            root_filesystem.filesystem_group,
            cache_filesystem.cache_set.filesystemgroup_set.first())
        bcache = root_partition.get_effective_filesystem().filesystem_group
        self.assertIsNotNone(bcache)
        self.assertThat(
            bcache.virtual_device.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test_configure_storage_creates_bcache_layout_without_partition(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        layout = BcacheStorageLayout(node, {
            "cache_no_part": True,
            })
        layout.configure()

        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[2]
        self.assertEquals(
            FILESYSTEM_TYPE.BCACHE_BACKING,
            root_partition.get_effective_filesystem().fstype)
        self.assertEquals(
            FILESYSTEM_TYPE.BCACHE_CACHE,
            ssd.get_effective_filesystem().fstype)
        root_filesystem = root_partition.get_effective_filesystem()
        self.assertEquals(
            FILESYSTEM_GROUP_TYPE.BCACHE,
            root_filesystem.filesystem_group.group_type)
        ssd_filesystem = ssd.get_effective_filesystem()
        self.assertEquals(
            root_partition.get_effective_filesystem().filesystem_group,
            ssd_filesystem.cache_set.filesystemgroup_set.first())
        bcache = root_partition.get_effective_filesystem().filesystem_group
        self.assertThat(
            bcache.virtual_device.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))

    def test_configure_storage_creates_bcache_layout_with_cache_mode(self):
        node = make_Node_with_uefi_boot_method()
        boot_disk = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE)
        ssd = factory.make_PhysicalBlockDevice(
            node=node, size=LARGE_BLOCK_DEVICE, tags=['ssd'])
        cache_mode = factory.pick_enum(CACHE_MODE_TYPE)
        layout = BcacheStorageLayout(node, {
            "cache_no_part": True,
            "cache_mode": cache_mode,
            })
        layout.configure()

        partition_table = boot_disk.get_partitiontable()
        partitions = partition_table.partitions.order_by('id').all()
        root_partition = partitions[2]
        self.assertEquals(
            FILESYSTEM_TYPE.BCACHE_BACKING,
            root_partition.get_effective_filesystem().fstype)
        self.assertEquals(
            FILESYSTEM_TYPE.BCACHE_CACHE,
            ssd.get_effective_filesystem().fstype)
        root_filesystem = root_partition.get_effective_filesystem()
        self.assertEquals(
            FILESYSTEM_GROUP_TYPE.BCACHE,
            root_filesystem.filesystem_group.group_type)
        ssd_filesystem = ssd.get_effective_filesystem()
        self.assertEquals(
            root_partition.get_effective_filesystem().filesystem_group,
            ssd_filesystem.cache_set.filesystemgroup_set.first())
        bcache = root_partition.get_effective_filesystem().filesystem_group
        self.assertEquals(cache_mode, bcache.cache_mode)
        self.assertThat(
            bcache.virtual_device.get_effective_filesystem(),
            MatchesStructure.byEquality(
                fstype=FILESYSTEM_TYPE.EXT4,
                label="root",
                mount_point="/",
                ))