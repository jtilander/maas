# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Cisco UCS Power Driver."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from provisioningserver.drivers.hardware.ucsm import (
    power_control_ucsm,
    power_state_ucsm,
)
from provisioningserver.drivers.power import PowerDriver


def extract_ucsm_parameters(context):
    url = context.get('power_address')
    username = context.get('power_user')
    password = context.get('power_pass')
    uuid = context.get('uuid')
    return url, username, password, uuid


class UCSMPowerDriver(PowerDriver):

    name = 'ucsm'
    description = "Cisco UCS Power Driver."
    settings = []

    def detect_missing_packages(self):
        # uses urllib2 http client - nothing to look for!
        return []

    def power_on(self, system_id, context):
        """Power on UCSM node."""
        url, username, password, uuid = extract_ucsm_parameters(context)
        power_control_ucsm(
            url, username, password, uuid, maas_power_mode='on')

    def power_off(self, system_id, context):
        """Power off UCSM node."""
        url, username, password, uuid = extract_ucsm_parameters(context)
        power_control_ucsm(
            url, username, password, uuid, maas_power_mode='off')

    def power_query(self, system_id, context):
        """Power query UCSM node."""
        url, username, password, uuid = extract_ucsm_parameters(context)
        return power_state_ucsm(url, username, password, uuid)