# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Interface Forms."""

__all__ = [
    "BondInterfaceForm",
    "InterfaceForm",
    "PhysicalInterfaceForm",
    "VLANInterfaceForm",
]

from django import forms
from django.core.exceptions import ValidationError
from maasserver.enum import (
    BOND_LACP_RATE_CHOICES,
    BOND_MODE_CHOICES,
    BOND_XMIT_HASH_POLICY_CHOICES,
    INTERFACE_TYPE,
    NODE_TYPE,
)
from maasserver.forms import (
    MAASModelForm,
    set_form_error,
)
from maasserver.models.interface import (
    BondInterface,
    BridgeInterface,
    build_vlan_interface_name,
    Interface,
    InterfaceRelationship,
    PhysicalInterface,
    VLANInterface,
)
from maasserver.utils.forms import compose_invalid_choice_text


class InterfaceForm(MAASModelForm):
    """Base Interface creation/edition form.

    Do not use this directly, instead, use the specialized
    versions defined below.
    """

    type = None

    parents = forms.ModelMultipleChoiceField(
        queryset=None, required=False)

    # Linux doesn't allow lower than 552 for the MTU.
    mtu = forms.IntegerField(min_value=552, required=False)

    # IPv6 parameters.
    accept_ra = forms.NullBooleanField(required=False)
    autoconf = forms.NullBooleanField(required=False)

    @staticmethod
    def get_interface_form(type):
        try:
            return INTERFACE_FORM_MAPPING[type]
        except KeyError:
            raise ValidationError({'type': [
                "Invalid interface type '%s'." % type]})

    class Meta:
        model = Interface

        fields = (
            'vlan',
            'tags',
            )

    def __init__(self, *args, **kwargs):
        self.node = kwargs.pop("node", None)
        super(InterfaceForm, self).__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        if instance is not None:
            self.node = instance.get_node()
            self.parents = instance.parents
        if self.node is None:
            raise ValueError(
                "instance or node is required for the InterfaceForm")
        self.fields['parents'].queryset = self.node.interface_set.all()

    def save(self, *args, **kwargs):
        """Persist the interface into the database."""
        created = self.instance.id is None
        interface = super(InterfaceForm, self).save(commit=True)
        if 'parents' in self.data:
            parents = self.cleaned_data.get('parents')
            existing_parents = set(interface.parents.all())
            if parents:
                parents = set(parents)
                for parent_to_add in parents.difference(existing_parents):
                    rel = InterfaceRelationship(
                        child=interface, parent=parent_to_add)
                    rel.save()
                for parent_to_del in existing_parents.difference(parents):
                    rel = interface.parent_relationships.filter(
                        parent=parent_to_del)
                    rel.delete()
        # Allow setting the VLAN to None.
        new_vlan = self.cleaned_data.get('vlan')
        vlan_was_set = 'vlan' in self.data
        if new_vlan is None and vlan_was_set:
            interface.vlan = new_vlan
        self.set_extra_parameters(interface, created)
        interface.save()
        if created:
            interface.ensure_link_up()
        return Interface.objects.get(id=interface.id)

    def fields_ok(self, field_list):
        """Return True if none of the fields is in error thus far."""
        return all(
            field not in self.errors for field in field_list)

    def get_clean_parents(self):
        if 'parents' in self.data or self.instance.id is None:
            parents = self.cleaned_data.get('parents')
        else:
            parents = self.instance.parents.all()
        return parents

    def clean_interface_name_uniqueness(self, name):
        node_interfaces = self.node.interface_set.filter(name=name)
        if self.instance is not None and self.instance.id is not None:
            node_interfaces = node_interfaces.exclude(
                id=self.instance.id)
        if node_interfaces.exists():
            msg = "Node %s already has an interface named '%s'." % (
                self.node, name)
            set_form_error(self, 'name', msg)

    def clean_parents_all_same_node(self, parents):
        if parents:
            parent_nodes = set(parent.get_node() for parent in parents)
            if len(parent_nodes) > 1:
                msg = "Parents are related to different nodes."
                set_form_error(self, 'name', msg)

    def clean(self):
        cleaned_data = super(InterfaceForm, self).clean()
        self.clean_parents_all_same_node(cleaned_data.get('parents'))
        return cleaned_data

    def _set_param(self, interface, key):
        """Helper to set parameters on an interface."""
        value = self.cleaned_data.get(key, None)
        if value is not None:
            interface.params[key] = value
        elif self.data.get(key) == '':
            interface.params.pop(key, None)

    def set_extra_parameters(self, interface, created):
        """Sets the extra parameters on the `interface`'s params property."""
        if not interface.params:
            interface.params = {}
        self._set_param(interface, "mtu")
        self._set_param(interface, "accept_ra")
        self._set_param(interface, "autoconf")


class ControllerInterfaceForm(MAASModelForm):
    """Interface update form for controllers."""

    type = None
    parents = None

    class Meta:
        model = Interface
        fields = (
            'vlan',
            )

    def save(self, *args, **kwargs):
        """Persist the interface into the database."""
        interface = super(ControllerInterfaceForm, self).save(commit=False)
        # Allow setting the VLAN to None.
        new_vlan = self.cleaned_data.get('vlan')
        vlan_was_set = 'vlan' in self.data
        if new_vlan is None and vlan_was_set:
            interface.vlan = new_vlan
        interface.save()
        return interface


class PhysicalInterfaceForm(InterfaceForm):
    """Form used to create/edit a physical interface."""

    enabled = forms.NullBooleanField(required=False)

    class Meta:
        model = PhysicalInterface
        fields = InterfaceForm.Meta.fields + (
            'mac_address',
            'name',
            'enabled',
        )

    def __init__(self, *args, **kwargs):
        super(PhysicalInterfaceForm, self).__init__(*args, **kwargs)
        # Force MAC to be non-null.
        self.fields['mac_address'].required = True

    def _get_validation_exclusions(self):
        # The instance is created just before this in django. The only way to
        # get the validation to pass on a newly created interface is to set the
        # node in the interface here.
        self.instance.node = self.node
        return super(PhysicalInterfaceForm, self)._get_validation_exclusions()

    def clean_parents(self):
        parents = self.get_clean_parents()
        if parents is None:
            return
        if len(parents) > 0:
            raise ValidationError("A physical interface cannot have parents.")

    def clean_vlan(self):
        new_vlan = self.cleaned_data.get('vlan')
        if new_vlan and new_vlan.fabric.get_default_vlan() != new_vlan:
            # A device's physical interface can be connected to a tagged VLAN.
            # This is because a container or VM could be bridged on the machine
            # to a tagged VLAN interface. See lp:1572070 for details.
            if self.node.node_type != NODE_TYPE.DEVICE:
                raise ValidationError(
                    "A physical interface can only belong to an "
                    "untagged VLAN.")
        return new_vlan

    def clean(self):
        cleaned_data = super(PhysicalInterfaceForm, self).clean()
        new_name = cleaned_data.get('name')
        if self.fields_ok(['name']) and new_name:
            self.clean_interface_name_uniqueness(new_name)
        return cleaned_data


class VLANInterfaceForm(InterfaceForm):
    """Form used to create/edit a VLAN interface."""

    class Meta:
        model = VLANInterface
        fields = InterfaceForm.Meta.fields

    def clean_parents(self):
        parents = self.get_clean_parents()
        if parents is None:
            return
        if len(parents) != 1:
            raise ValidationError(
                "A VLAN interface must have exactly one parent.")
        if parents[0].type == INTERFACE_TYPE.VLAN:
            raise ValidationError(
                "A VLAN interface can't have another VLAN interface as "
                "parent.")
        parent_has_bond_children = [
            rel.child
            for rel in parents[0].children_relationships.all()
            if rel.child.type == INTERFACE_TYPE.BOND
        ]
        if parent_has_bond_children:
            raise ValidationError(
                "A VLAN interface can't have a parent that is already "
                "in a bond.")
        return parents

    def clean_vlan(self):
        created = self.instance.id is None
        new_vlan = self.cleaned_data.get('vlan')
        vlan_was_set = 'vlan' in self.data
        if (created and new_vlan is None) or (
                not created and new_vlan is None and vlan_was_set):
            raise ValidationError(
                "A VLAN interface must be connected to a tagged VLAN.")
        if new_vlan and new_vlan.fabric.get_default_vlan() == new_vlan:
            raise ValidationError(
                "A VLAN interface can only belong to a tagged VLAN.")
        return new_vlan

    def clean(self):
        cleaned_data = super(VLANInterfaceForm, self).clean()
        if self.fields_ok(['vlan', 'parents']):
            new_vlan = self.cleaned_data.get('vlan')
            if new_vlan:
                # VLAN needs to be the in the same fabric as the parent.
                parent = self.cleaned_data.get('parents')[0]
                if parent.vlan.fabric_id != new_vlan.fabric_id:
                    set_form_error(
                        self, "vlan",
                        "A VLAN interface can only belong to a tagged VLAN on "
                        "the same fabric as its parent interface.")
                name = build_vlan_interface_name(
                    self.cleaned_data.get('parents').first(), new_vlan)
                self.clean_interface_name_uniqueness(name)
        return cleaned_data


class ChildInterfaceForm(InterfaceForm):
    """Form used to create "child" interfaces (that is, interfaces which
    require their "parent" interfaces in order to exist, such as bonds and
    bridges.
    """

    def clean_parents(self):
        """Validate that child interfaces cannot be created unless at least one
        parent is present.
        """
        parents = self.get_clean_parents()
        if parents is None:
            return
        # Ensure support for parthenogenesis.
        if len(parents) < 1:
            raise ValidationError(
                "A %s interface must have one or more parents." %
                self.Meta.model.get_type())
        return parents

    def _set_default_child_mac(self, parents):
        """Sets the value of self.cleaned_data['mac_address'] based on either
        the first parent (if the child interface is new), or a remaining parent
        (if a parent with the current MAC was removed).
        """
        if self.instance.id is not None:
            parent_macs = {
                parent.mac_address.get_raw(): parent
                for parent in self.instance.parents.all()
                }
        else:
            parent_macs = {}
        mac_not_changed = (
            self.instance.id is not None and
            self.cleaned_data["mac_address"] == self.instance.mac_address
        )
        if self.instance.id is None and 'mac_address' not in self.data:
            # New bond without mac_address set, set it to the first
            # parent mac_address.
            self.cleaned_data['mac_address'] = str(
                parents[0].mac_address)
        elif (mac_not_changed and
              self.instance.mac_address in parent_macs and
              parent_macs[self.instance.mac_address] not in parents):
            # Updating child where its mac_address comes from its parent
            # and that parent is no longer part of this child. Update
            # the mac_address to be one of the new parent MAC
            # addresses.
            self.cleaned_data['mac_address'] = str(
                parents[0].mac_address)

    def _set_default_vlan(self, parents):
        """When creating the child, set VLAN to the same as the first parent
        by default.
        """
        if self.instance.id is None:
            vlan = self.cleaned_data.get('vlan')
            if vlan is None:
                vlan = parents[0].vlan
                self.cleaned_data['vlan'] = vlan

    def _validate_parental_fidelity(self, parents):
        """Check that all of the parent interfaces are not already in a
        relationship before committing them to this child.
        """
        parents_with_other_children = {
            parent.name
            for parent in parents
            for rel in parent.children_relationships.all()
            if rel.child.id != self.instance.id
        }
        if parents_with_other_children:
            set_form_error(
                self, 'parents',
                "Interfaces already in-use: %s." % (
                    ', '.join(sorted(parents_with_other_children))))


class BondInterfaceForm(ChildInterfaceForm):
    """Form used to create/edit a bond interface."""

    bond_mode = forms.ChoiceField(
        choices=BOND_MODE_CHOICES, required=False,
        initial=BOND_MODE_CHOICES[0][0], error_messages={
            'invalid_choice': compose_invalid_choice_text(
                'bond_mode', BOND_MODE_CHOICES),
        })

    bond_miimon = forms.IntegerField(min_value=0, initial=100, required=False)

    bond_downdelay = forms.IntegerField(min_value=0, initial=0, required=False)

    bond_updelay = forms.IntegerField(min_value=0, initial=0, required=False)

    bond_lacp_rate = forms.ChoiceField(
        choices=BOND_LACP_RATE_CHOICES, required=False,
        initial=BOND_LACP_RATE_CHOICES[0][0], error_messages={
            'invalid_choice': compose_invalid_choice_text(
                'bond_lacp_rate', BOND_LACP_RATE_CHOICES),
        })

    bond_xmit_hash_policy = forms.ChoiceField(
        choices=BOND_XMIT_HASH_POLICY_CHOICES, required=False,
        initial=BOND_XMIT_HASH_POLICY_CHOICES[0][0], error_messages={
            'invalid_choice': compose_invalid_choice_text(
                'bond_xmit_hash_policy', BOND_XMIT_HASH_POLICY_CHOICES),
        })

    class Meta:
        model = BondInterface
        fields = InterfaceForm.Meta.fields + (
            'mac_address',
            'name',
        )

    def clean_vlan(self):
        new_vlan = self.cleaned_data.get('vlan')
        if new_vlan and new_vlan.fabric.get_default_vlan() != new_vlan:
            raise ValidationError(
                "A bond interface can only belong to an untagged VLAN.")
        return new_vlan

    def clean(self):
        cleaned_data = super().clean()
        if self.fields_ok(['vlan', 'parents']):
            parents = self.cleaned_data.get('parents')
            # Set the mac_address if its missing and the interface is being
            # created.
            if parents:
                self._set_default_child_mac(parents)
                self._validate_parental_fidelity(parents)
                self._set_default_vlan(parents)
                self._validate_parent_vlans_match(parents)
        return cleaned_data

    def _validate_parent_vlans_match(self, parents):
        # When creating the bond set VLAN to the same as the parents
        # and check that the parents all belong to the same VLAN.
        if self.instance.id is None:
            vlan = self.cleaned_data.get('vlan')
            parent_vlans = {
                parent.vlan
                for parent in parents
                }
            if parent_vlans != set([vlan]):
                set_form_error(
                    self, 'parents',
                    "All parents must belong to the same VLAN.")

    def set_extra_parameters(self, interface, created):
        """Set the bond parameters as well."""
        super().set_extra_parameters(interface, created)
        # Set all the bond_* parameters.
        bond_fields = [
            field_name
            for field_name in self.fields
            if field_name.startswith("bond_")
        ]
        for bond_field in bond_fields:
            value = self.cleaned_data.get(bond_field)
            if (value is not None and
                    isinstance(value, str) and
                    len(value) > 0 and not value.isspace()):
                interface.params[bond_field] = value
            elif (value is not None and
                    not isinstance(value, str)):
                interface.params[bond_field] = value
            elif created:
                interface.params[bond_field] = self.fields[bond_field].initial


class BridgeInterfaceForm(ChildInterfaceForm):
    """Form used to create/edit a bridge interface."""

    bridge_stp = forms.NullBooleanField(initial=False, required=False)

    bridge_fd = forms.IntegerField(min_value=0, initial=15, required=False)

    class Meta:
        model = BridgeInterface
        fields = InterfaceForm.Meta.fields + (
            'mac_address',
            'name',
        )

    def clean_parents(self):
        parents = self.get_clean_parents()
        if parents is None:
            return
        if len(parents) != 1:
            raise ValidationError(
                "A bridge interface must have exactly one parent.")
        if parents[0].type == INTERFACE_TYPE.BRIDGE:
            raise ValidationError(
                "A bridge interface can't have another bridge interface as "
                "parent.")
        instance_id = None if self.instance is None else self.instance.id
        bond_or_bridge = {INTERFACE_TYPE.BOND, INTERFACE_TYPE.BRIDGE}
        parent_has_bad_children = any(
            rel.child.type in bond_or_bridge and rel.child.id != instance_id
            for rel in parents[0].children_relationships.all())
        if parent_has_bad_children:
            raise ValidationError(
                "A bridge interface can't have a parent that is already "
                "in a bond or a bridge.")
        return parents

    def clean(self):
        cleaned_data = super().clean()
        if self.fields_ok(['vlan', 'parents']):
            parents = self.cleaned_data.get('parents')
            # Set the mac_address if its missing and the interface is being
            # created.
            if parents:
                self._set_default_child_mac(parents)
                self._validate_parental_fidelity(parents)
                self._set_default_vlan(parents)
        return cleaned_data

    def set_extra_parameters(self, interface, created):
        """Set the bridge parameters as well."""
        super().set_extra_parameters(interface, created)
        # Set all the bridge_* parameters.
        bridge_fields = [
            field_name
            for field_name in self.fields
            if field_name.startswith("bridge_")
        ]
        for bridge_field in bridge_fields:
            value = self.cleaned_data.get(bridge_field)
            if (value is not None and
                    isinstance(value, str) and
                    len(value) > 0 and not value.isspace()):
                interface.params[bridge_field] = value
            elif (value is not None and
                    not isinstance(value, str)):
                interface.params[bridge_field] = value
            elif created:
                interface.params[bridge_field] = (
                    self.fields[bridge_field].initial)

INTERFACE_FORM_MAPPING = {
    INTERFACE_TYPE.PHYSICAL: PhysicalInterfaceForm,
    INTERFACE_TYPE.VLAN: VLANInterfaceForm,
    INTERFACE_TYPE.BOND: BondInterfaceForm,
    INTERFACE_TYPE.BRIDGE: BridgeInterfaceForm,
}
