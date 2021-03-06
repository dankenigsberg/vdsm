#
# Copyright 2015 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import xml.etree.ElementTree as ET

from vdsm import utils
from vdsm import supervdsm
from vdsm.hostdev import get_device_params, detach_detachable, \
    pci_address_to_name, scsi_address_to_adapter, reattach_detachable
from . import core
from . import hwclass
from .. import vmxml


class PciDevice(core.Base):
    __slots__ = ('address', 'hostAddress', 'bootOrder', '_deviceParams',
                 'name', 'numa_node')

    def __init__(self, conf, log, **kwargs):
        super(PciDevice, self).__init__(conf, log, **kwargs)

        self._deviceParams = get_device_params(self.device)
        self.hostAddress = self._deviceParams.get('address')
        self.numa_node = self._deviceParams.get('numa_node', None)
        self.name = self.device
        self.is_hostdevice = True

    def setup(self):
        self.log.info('Detaching device %s from the host.' % self.device)
        detach_detachable(self.device)

    def teardown(self):
        supervdsm.getProxy().rmAppropriateIommuGroup(
            self._deviceParams['iommu_group'])

    @property
    def _xpath(self):
        """
        Returns xpath to the device in libvirt dom xml.
        The path is relative to the root element.
        """
        address_fields = []
        for key, value in self.hostAddress.items():
            padding, base = '02', '0x'
            if key == 'domain':
                base = '0x'
                padding = '04'
            elif key == 'function':
                base = '0x'
                padding = ''

            address_fields.append('[@{key}="{base}{value:{padding}}"]'.format(
                key=key, value=int(value), base=base, padding=padding))

        return './devices/hostdev/source/address{}'.format(
            ''.join(address_fields))

    def is_attached_to(self, xml_string):
        dom = ET.fromstring(xml_string)
        return bool(dom.findall(self._xpath))

    def getXML(self):
        """
        Create domxml for a host device.

        <devices>
            <hostdev mode='subsystem' type='pci' managed='no'>
            <source>
                <address domain='0x0000' bus='0x06' slot='0x02'
                function='0x0'/>
            </source>
            <boot order='1'/>
            </hostdev>
        </devices>
        """

        if utils.tobool(self.specParams.get('iommuPlaceholder', False)):
            raise core.SkipDevice

        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(managed='no', mode='subsystem', type='pci')
        source = hostdev.appendChildWithArgs('source')

        source.appendChildWithArgs('address', **self.hostAddress)

        if hasattr(self, 'bootOrder'):
            hostdev.appendChildWithArgs('boot', order=self.bootOrder)

        if hasattr(self, 'address'):
            hostdev.appendChildWithArgs('address', **self.address)

        return hostdev

    @classmethod
    def update_from_xml(cls, vm, device_conf, device_xml):
        alias = vmxml.find_attr(device_xml, 'alias', 'name')
        address = vmxml.device_address(device_xml)
        source = vmxml.find_first(device_xml, 'source')
        device = pci_address_to_name(**vmxml.device_address(source))

        # We can assume the device name to be correct since we're
        # inspecting source element. For the address, we may look at
        # both addresses and determine the correct one.
        if pci_address_to_name(**address) == device:
            address = vmxml.device_address(device_xml, 1)

        known_device = False
        for dev in device_conf:
            if dev.device == device:
                dev.alias = alias
                dev.address = address
                known_device = True

        for dev in vm.conf['devices']:
            if dev['device'] == device:
                dev['alias'] = alias
                dev['address'] = address

        if not known_device:
            device = pci_address_to_name(**vmxml.device_address(source))

            hostdevice = {
                'type': hwclass.HOSTDEV,
                'device': device,
                'alias': alias,
                'address': address,
            }
            vm.conf['devices'].append(hostdevice)


class UsbDevice(core.Base):
    __slots__ = ('address', 'hostAddress', 'bootOrder', '_deviceParams',
                 'name', 'numa_node')

    def __init__(self, conf, log, **kwargs):
        super(UsbDevice, self).__init__(conf, log, **kwargs)

        device_params = get_device_params(self.device)
        self.hostAddress = device_params.get('address')
        self.numa_node = None
        self.name = self.device
        self.is_hostdevice = True

    def setup(self):
        detach_detachable(self.device)

    def teardown(self):
        reattach_detachable(self.device)

    @property
    def _xpath(self):
        """
        Returns xpath to the device in libvirt dom xml.
        The path is relative to the root element.
        """
        address_fields = []
        for key, value in self.hostAddress.items():
            address_fields.append('[@{key}="{value}"]'.format(
                key=key, value=int(value)))

        return './devices/hostdev/source/address{}'.format(
            ''.join(address_fields))

    def is_attached_to(self, xml_string):
        dom = ET.fromstring(xml_string)
        return bool(dom.findall(self._xpath))

    def getXML(self):
        """
        Create domxml for a host device.

        <hostdev managed="no" mode="subsystem" type="usb">
                <source>
                        <address bus="1" device="2"/>
                </source>
        </hostdev>
        """
        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(managed='no', mode='subsystem', type='usb')
        source = hostdev.appendChildWithArgs('source')

        source.appendChildWithArgs('address', **self.hostAddress)

        if hasattr(self, 'bootOrder'):
            hostdev.appendChildWithArgs('boot', order=self.bootOrder)

        if hasattr(self, 'address'):
            hostdev.appendChildWithArgs('address', **self.address)

        return hostdev

    @classmethod
    def update_from_xml(cls, vm, device_conf, device_xml):
        alias = vmxml.find_attr(device_xml, 'alias', 'name')
        host_address = vmxml.device_address(device_xml)

        # The routine is quite unusual because we cannot directly
        # reconstruct the unique name. Therefore, we first look up
        # corresponding device object address,
        for dev in device_conf:
            if host_address == dev.hostAddress:
                dev.alias = alias
                device = dev.device
                # RHBZ#1215968
                # dev.address = vmxml.device_address(device_xml, 1)

        # and use that to identify the device in self.conf.
        for dev in vm.conf['devices']:
            if dev['device'] == device:
                dev['alias'] = alias
                # RHBZ#1215968
                # dev['address'] = vmxml.device_address(device_xml, 1)

        # This has an unfortunate effect that we will not be able to look
        # up any previously undefined devices, because there is no easy
        # way of reconstructing the udev name we use as unique id.


class ScsiDevice(core.Base):
    __slots__ = ('address', 'hostAddress', 'bootOrder', '_deviceParams',
                 'name', 'bus_address', 'adapter', 'numa_node')

    def __init__(self, conf, log, **kwargs):
        super(ScsiDevice, self).__init__(conf, log, **kwargs)

        device_params = get_device_params(self.device)
        self.hostAddress = device_params.get('address')
        self.numa_node = None
        self.name = self.device
        self.bus_address, self.adapter = scsi_address_to_adapter(
            self.hostAddress)
        self.is_hostdevice = True

    def setup(self):
        detach_detachable(self.device)

    def teardown(self):
        reattach_detachable(self.device)

    def is_attached_to(self, xml_string):
        address_fields = []
        for key, value in self.bus_address.items():
            address_fields.append('[@{key}="{value}"]'.format(
                key=key, value=int(value)))

        dom = ET.fromstring(xml_string)
        for src in dom.findall('./devices/hostdev/source'):
            address = src.find('address{}'.format(''.join(address_fields)))
            adapter = src.find('adapter[@name="{}"]'.format(self.adapter))

            if (address is not None and adapter is not None):
                return True

        return False

    def getXML(self):
        """
        Create domxml for a host device.

        <hostdev managed="no" mode="subsystem" rawio="yes" type="scsi">
            <source>
                <adapter name="scsi_host4"/>
                <address bus="0" target="0" unit="0"/>
            </source>
        </hostdev>
        """
        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(managed='no', mode='subsystem', type='scsi')
        source = hostdev.appendChildWithArgs('source')

        # This must be done *before* creating the address element, as we need
        # remove the 'host' key from real address.
        source.appendChildWithArgs('adapter', name=self.adapter)
        hostdev.setAttr('rawio', 'yes')

        source.appendChildWithArgs('address', **self.bus_address)

        if hasattr(self, 'bootOrder'):
            hostdev.appendChildWithArgs('boot', order=self.bootOrder)

        if hasattr(self, 'address'):
            hostdev.appendChildWithArgs('address', **self.address)

        return hostdev

    @classmethod
    def update_from_xml(cls, vm, device_conf, device_xml):
        alias = vmxml.find_attr(device_xml, 'alias', 'name')
        bus_address = vmxml.device_address(device_xml)
        source = vmxml.find_first(device_xml, 'source')
        adapter = vmxml.find_attr(source, 'adapter', 'name')

        # The routine is quite unusual because we cannot directly
        # reconstruct the unique name. Therefore, we first look up
        # corresponding device object address,
        for dev in device_conf:
            if (hasattr(dev, 'bus_address') and
                    bus_address == dev.bus_address and adapter == dev.adapter):
                dev.alias = alias
                device = dev.device
                dev.address = vmxml.device_address(device_xml, 1)

        # and use that to identify the device in self.conf.
        for dev in vm.conf['devices']:
            if dev['device'] == device:
                dev['alias'] = alias
                dev['address'] = vmxml.device_address(device_xml, 1)

        # This has an unfortunate effect that we will not be able to look
        # up any previously undefined devices, because there is no easy
        # way of reconstructing the udev name we use as unique id.


class HostDevice(core.Base):
    __slots__ = ('_device',)

    _DEVICE_MAPPING = {
        'pci': PciDevice,
        'usb_device': UsbDevice,
        'usb': UsbDevice,
        'scsi': ScsiDevice,
    }

    def __new__(cls, conf, log, **kwargs):
        device_params = get_device_params(kwargs['device'])
        device = cls._DEVICE_MAPPING[
            device_params['capability']](conf, log, **kwargs)
        return device

    @classmethod
    def update_device_info(cls, vm, device_conf):
        for device_xml in vm.domain.get_device_elements('hostdev'):
            device_type = vmxml.attr(device_xml, 'type')
            cls._DEVICE_MAPPING[device_type].update_from_xml(
                vm, device_conf, device_xml)
