# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `provisioningserver.drivers.power.ipmi`."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from maastesting.factory import factory
from maastesting.matchers import MockCalledOnceWith
from maastesting.testcase import MAASTestCase
from provisioningserver.drivers.power import (
    moonshot as moonshot_module,
    PowerActionError,
    PowerFatalError,
)
from provisioningserver.drivers.power.moonshot import MoonshotIPMIPowerDriver
from provisioningserver.utils.shell import (
    ExternalProcessError,
    has_command_available,
)
from testtools.matchers import Equals


def make_parameters():
    return {
        'power_hwaddress': factory.make_name('power_hwaddress'),
        'power_address': factory.make_name('power_address'),
        'power_user': factory.make_name('power_user'),
        'power_pass': factory.make_name('power_pass'),
        'ipmitool': factory.make_name('ipmitool'),
    }


def make_ipmitool_command(
        power_change, power_hwaddress, power_address,
        power_user, power_pass, ipmitool):
    return (
        ipmitool, '-I', 'lanplus', '-H', power_address, '-U', power_user,
        '-P', power_pass, power_hwaddress, 'power', power_change
    )


class TestMoonshotIPMIPowerDriver(MAASTestCase):

    def test_missing_packages(self):
        mock = self.patch(has_command_available)
        mock.return_value = False
        driver = moonshot_module.MoonshotIPMIPowerDriver()
        missing = driver.detect_missing_packages()
        self.assertItemsEqual(['freeipmi-tools'], missing)

    def test_no_missing_packages(self):
        mock = self.patch(has_command_available)
        mock.return_value = True
        driver = moonshot_module.MoonshotIPMIPowerDriver()
        missing = driver.detect_missing_packages()
        self.assertItemsEqual([], missing)

    def test__issue_ipmitool_command_issues_power_on(self):
        params = make_parameters()
        power_change = 'on'
        ipmitool_command = make_ipmitool_command(power_change, **params)
        moonshot_driver = MoonshotIPMIPowerDriver()
        call_and_check_mock = self.patch(moonshot_module, 'call_and_check')
        call_and_check_mock.return_value = power_change

        result = moonshot_driver._issue_ipmitool_command(
            power_change, **params)

        self.expectThat(
            call_and_check_mock, MockCalledOnceWith(ipmitool_command))
        self.expectThat(result, Equals(power_change))

    def test__issue_ipmitool_command_issues_power_off(self):
        params = make_parameters()
        power_change = 'off'
        ipmitool_command = make_ipmitool_command(power_change, **params)
        moonshot_driver = MoonshotIPMIPowerDriver()
        call_and_check_mock = self.patch(moonshot_module, 'call_and_check')
        call_and_check_mock.return_value = power_change

        result = moonshot_driver._issue_ipmitool_command(
            power_change, **params)

        self.expectThat(
            call_and_check_mock, MockCalledOnceWith(ipmitool_command))
        self.expectThat(result, Equals(power_change))

    def test__issue_ipmitool_command_raises_power_action_error(self):
        params = make_parameters()
        power_change = 'other'
        ipmitool_command = make_ipmitool_command(power_change, **params)
        moonshot_driver = MoonshotIPMIPowerDriver()
        call_and_check_mock = self.patch(moonshot_module, 'call_and_check')
        call_and_check_mock.return_value = power_change

        self.assertRaises(
            PowerActionError, moonshot_driver._issue_ipmitool_command,
            power_change, **params)
        self.expectThat(
            call_and_check_mock, MockCalledOnceWith(ipmitool_command))

    def test__issue_ipmitool_raises_power_fatal_error(self):
        params = make_parameters()
        moonshot_driver = MoonshotIPMIPowerDriver()
        call_and_check_mock = self.patch(moonshot_module, 'call_and_check')
        call_and_check_mock.side_effect = (
            ExternalProcessError(1, "ipmitool something"))

        self.assertRaises(
            PowerFatalError, moonshot_driver._issue_ipmitool_command,
            'status', **params)

    def test_power_on_calls__issue_ipmitool_command(self):
        params = make_parameters()
        moonshot_driver = MoonshotIPMIPowerDriver()
        _issue_ipmitool_command_mock = self.patch(
            moonshot_driver, '_issue_ipmitool_command')
        system_id = factory.make_name('system_id')
        moonshot_driver.power_on(system_id, **params)

        self.assertThat(
            _issue_ipmitool_command_mock, MockCalledOnceWith('on', **params))

    def test_power_off_calls__issue_ipmitool_command(self):
        params = make_parameters()
        moonshot_driver = MoonshotIPMIPowerDriver()
        _issue_ipmitool_command_mock = self.patch(
            moonshot_driver, '_issue_ipmitool_command')
        system_id = factory.make_name('system_id')
        moonshot_driver.power_off(system_id, **params)

        self.assertThat(
            _issue_ipmitool_command_mock, MockCalledOnceWith('off', **params))

    def test_power_query_calls__issue_ipmitool_command(self):
        params = make_parameters()
        moonshot_driver = MoonshotIPMIPowerDriver()
        _issue_ipmitool_command_mock = self.patch(
            moonshot_driver, '_issue_ipmitool_command')
        system_id = factory.make_name('system_id')
        moonshot_driver.power_query(system_id, **params)

        self.assertThat(
            _issue_ipmitool_command_mock, MockCalledOnceWith(
                'status', **params))