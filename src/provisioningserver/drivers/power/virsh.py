# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Virsh Power Driver."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from provisioningserver.drivers.hardware.virsh import (
    power_control_virsh,
    power_state_virsh,
)
from provisioningserver.drivers.power import PowerDriver
from provisioningserver.utils import shell


REQUIRED_PACKAGES = [["virsh", "libvirt-bin"],
                     ["virt-login-shell", "libvirt-bin"]]


def extract_virsh_parameters(context):
    poweraddr = context.get('power_address')
    machine = context.get('power_id')
    password = context.get('power_pass')
    return poweraddr, machine, password


class VirshPowerDriver(PowerDriver):

    name = 'virsh'
    description = "Virsh Power Driver."
    settings = []

    def detect_missing_packages(self):
        missing_packages = set()
        for binary, package in REQUIRED_PACKAGES:
            if not shell.has_command_available(binary):
                missing_packages.add(package)
        return list(missing_packages)

    def power_on(self, system_id, context):
        """Power on Virsh node."""
        power_change = 'on'
        poweraddr, machine, password = extract_virsh_parameters(context)
        power_control_virsh(
            poweraddr, machine, power_change, password)

    def power_off(self, system_id, context):
        """Power off Virsh node."""
        power_change = 'off'
        poweraddr, machine, password = extract_virsh_parameters(context)
        power_control_virsh(
            poweraddr, machine, power_change, password)

    def power_query(self, system_id, context):
        """Power query Virsh node."""
        poweraddr, machine, password = extract_virsh_parameters(context)
        return power_state_virsh(poweraddr, machine, password)