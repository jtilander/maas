# Copyright 2014-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Django command: Edit the named.conf.options file so that it includes
the named.conf.options.inside.maas file, which contains the 'forwarders'
setting.
"""

__all__ = [
    'Command',
    ]

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import os
import shutil
import sys
from textwrap import dedent

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from maasserver.models import Config
from provisioningserver.dns.config import MAAS_NAMED_CONF_OPTIONS_INSIDE_NAME
from provisioningserver.utils.isc import (
    ISCParseException,
    make_isc_string,
    parse_isc_string,
)


class Command(BaseCommand):

    help = " ".join(dedent("""\
    Edit the named.conf.options file so that it includes the
    named.conf.options.inside.maas file, which contains the 'forwarders' and
    'dnssec-validation' settings. A backup of the old file will be made with
    the suffix '.maas-YYYY-MM-DDTHH:MM:SS.mmmmmm'. All configuration files are
    treated as 7-bit ASCII; it's not clear what else BIND will tolerate. This
    program must be run as root.
    """).splitlines())

    def add_arguments(self, parser):
        super(Command, self).add_arguments(parser)

        parser.add_argument(
            '--config-path', dest='config_path',
            default="/etc/bind/named.conf.options",
            help="Specify the configuration file to edit.")
        parser.add_argument(
            '--dry-run', dest='dry_run',
            default=False, action='store_true',
            help="Do not edit any configuration; instead, print to stdout the "
                 "actions that would be performed, and/or the new "
                 "configuration that would be written.")
        parser.add_argument(
            '--force', dest='force',
            default=False, action='store_true',
            help="Force the BIND configuration to be written, even if it "
                 "appears as though nothing has changed.")
        parser.add_argument(
            '--migrate-conflicting-options', default=False,
            dest='migrate_conflicting_options', action='store_true',
            help="**This option is now deprecated**. It no longer has any "
                 "effect and it may be removed in a future release.")

    def read_file(self, config_path):
        """Open the named file and return its contents as a string."""
        if not os.path.exists(config_path):
            raise CommandError("%s does not exist" % config_path)

        with open(config_path, "r", encoding="ascii") as fd:
            options_file = fd.read()
        return options_file

    def parse_file(self, config_path, options_file):
        """Read the named.conf.options file and parse it.

        Then insert the include statement that we need.
        """
        try:
            config_dict = parse_isc_string(options_file)
        except ISCParseException as e:
            raise CommandError("Failed to parse %s: %s" % (
                config_path, str(e))) from e
        options_block = config_dict.get("options", None)
        if options_block is None:
            # Something is horribly wrong with the file; bail out rather
            # than doing anything drastic.
            raise CommandError(
                "Can't find options {} block in %s, bailing out without "
                "doing anything." % config_path)
        return config_dict

    def set_up_include_statement(self, options_block, config_path):
        """Insert the 'include' directive into the parsed options."""
        dir = os.path.join(os.path.dirname(config_path), "maas")
        options_block['include'] = '"%s%s%s"' % (
            dir, os.path.sep, MAAS_NAMED_CONF_OPTIONS_INSIDE_NAME)

    def migrate_forwarders(self, options_block, dry_run, stdout):
        """Remove existing forwarders from the options block.

        It's a syntax error to have more than one in the combined
        configuration for named, so we just remove whatever was there.

        Migrate any forwarders in the configuration file to the MAAS config.
        """
        if 'forwarders' in options_block:
            bind_forwarders = options_block['forwarders']

            delete_forwarders = False
            if not dry_run:
                try:
                    config, created = Config.objects.get_or_create(
                        name='upstream_dns',
                        defaults={'value': ' '.join(bind_forwarders)})
                    if not created:
                        # A configuration value already exists, so add the
                        # additional values we found in the configuration
                        # file to MAAS.
                        if config.value is None:
                            config.value = ''
                        maas_forwarders = OrderedDict.fromkeys(
                            config.value.split())
                        maas_forwarders.update(bind_forwarders)
                        config.value = ' '.join(maas_forwarders)
                        config.save()
                    delete_forwarders = True
                except:
                    pass
            else:
                stdout.write(
                    "// Append to MAAS forwarders: %s\n"
                    % ' '.join(bind_forwarders))

            # Only delete forwarders from the config if MAAS was able to
            # migrate the options. Otherwise leave them in the original
            # config.
            if delete_forwarders:
                del options_block['forwarders']

    def migrate_dnssec_validation(self, options_block, dry_run, stdout):
        """Remove existing dnssec-validation from the options block.

        It's a syntax error to have more than one in the combined
        configuration for named so we just remove whatever was there.
        There is no data loss due to the backup file made later.

        Migrate this value in the configuration file to the MAAS config.
        """
        if 'dnssec-validation' in options_block:
            dnssec_validation = options_block['dnssec-validation']

            if not dry_run:
                try:
                    config, created = Config.objects.get_or_create(
                        name='dnssec_validation',
                        defaults={'value': dnssec_validation})
                    if not created:
                        # Update the MAAS configuration to reflect the new
                        # setting found in the configuration file.
                        config.value = dnssec_validation
                        config.save()
                except:
                    pass
            else:
                stdout.write(
                    "// Set MAAS dnssec_validation to: %s\n"
                    % dnssec_validation)
            # Always attempt to delete this option as MAAS will always create
            # a default for it.
            del options_block['dnssec-validation']

    def back_up_existing_file(self, config_path):
        now = datetime.now().isoformat()
        backup_destination = config_path + '.' + now
        try:
            shutil.copyfile(config_path, backup_destination)
        except IOError as e:
            raise CommandError(
                "Failed to make a backup of %s, exiting: %s" % (
                    config_path, str(e))) from e
        return backup_destination

    def write_new_named_conf_options(self, fd, backup_filename, new_content):
        fd.write("""\
//
// This file is managed by MAAS. Although MAAS attempts to preserve changes
// made here, it is possible to create conflicts that MAAS can not resolve.
//
// DNS settings available in MAAS (for example, forwarders and
// dnssec-validation) should be managed only in MAAS.
//
// The previous configuration file was backed up at:
//     %s
//
""" % backup_filename)
        fd.write(new_content)
        fd.write("\n")

    def handle(self, *args, **options):
        """Entry point for BaseCommand."""
        # Read stuff in, validate.
        config_path = options.get('config_path')
        dry_run = options.get('dry_run')
        force = options.get('force')
        stdout = options.get('stdout')
        if stdout is None:
            stdout = sys.stdout

        options_file = self.read_file(config_path)
        config_dict = self.parse_file(config_path, options_file)
        original_config = deepcopy(config_dict)

        options_block = config_dict['options']

        # Modify the configuration (if necessary).
        self.set_up_include_statement(options_block, config_path)

        # Attempt to migrate the conflicting options if there's
        # database connection.
        self.migrate_forwarders(options_block, dry_run, stdout)
        self.migrate_dnssec_validation(options_block, dry_run, stdout)

        # Re-parse the new configuration, so we can detect any changes.
        new_content = make_isc_string(config_dict)
        new_config = parse_isc_string(new_content)

        if original_config != new_config or force:
            # The configuration has changed. Back up and write new file.
            if dry_run:
                self.write_new_named_conf_options(
                    stdout, config_path, new_content)
            else:
                backup_filename = self.back_up_existing_file(config_path)
                with open(config_path, "w", encoding="ascii") as fd:
                    self.write_new_named_conf_options(
                        fd, backup_filename, new_content)
