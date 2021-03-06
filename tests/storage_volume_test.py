#
# Copyright 2016 Red Hat, Inc.
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

from __future__ import absolute_import

from monkeypatch import MonkeyPatchScope
from storagefakelib import FakeStorageDomainCache
from storagetestlib import FakeSD
from storagetestlib import fake_env
from testlib import expandPermutations, permutations
from testlib import make_uuid
from testlib import recorded
from testlib import VdsmTestCase

from vdsm.storage import constants as sc
from vdsm.storage import exception as se

from storage import resourceManager as rm
from storage import sd
from storage import volume

HOST_ID = 1
MB = 1048576


class FakeSDManifest(object):
    @recorded
    def acquireVolumeLease(self, hostId, imgUUID, volUUID):
        pass

    @recorded
    def releaseVolumeLease(self, imgUUID, volUUID):
        pass


@expandPermutations
class VolumeLeaseTest(VdsmTestCase):

    def test_properties(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        self.assertEqual(sd.getNamespace(sc.VOLUME_LEASE_NAMESPACE, 'dom'),
                         a.ns)
        self.assertEqual('vol', a.name)
        self.assertEqual(rm.EXCLUSIVE, a.mode)

    @permutations((
        (('domA', 'img', 'vol'), ('domB', 'img', 'vol')),
        (('dom', 'img', 'volA'), ('dom', 'img', 'volB')),
    ))
    def test_less_than(self, a, b):
        b = volume.VolumeLease(HOST_ID, *b)
        a = volume.VolumeLease(HOST_ID, *a)
        self.assertLess(a, b)

    def test_equality(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        b = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        self.assertEqual(a, b)

    def test_equality_different_image(self):
        a = volume.VolumeLease(HOST_ID, 'dom', 'img1', 'vol')
        b = volume.VolumeLease(HOST_ID, 'dom', 'img2', 'vol')
        self.assertEqual(a, b)

    def test_equality_different_host_id(self):
        a = volume.VolumeLease(0, 'dom', 'img', 'vol')
        b = volume.VolumeLease(1, 'dom', 'img', 'vol')
        self.assertEqual(a, b)

    def test_acquire_release(self):
        sdcache = FakeStorageDomainCache()
        manifest = FakeSDManifest()
        sdcache.domains['dom'] = FakeSD(manifest)
        expected = [('acquireVolumeLease', (HOST_ID, 'img', 'vol'), {}),
                    ('releaseVolumeLease', ('img', 'vol'), {})]
        with MonkeyPatchScope([(volume, 'sdCache', sdcache)]):
            lock = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
            lock.acquire()
            self.assertEqual(expected[:1], manifest.__calls__)
            lock.release()
            self.assertEqual(expected, manifest.__calls__)

    def test_repr(self):
        lock = volume.VolumeLease(HOST_ID, 'dom', 'img', 'vol')
        lock_string = str(lock)
        self.assertIn("VolumeLease", lock_string)
        self.assertIn("ns=04_lease_dom", lock_string)
        self.assertIn("name=vol", lock_string)
        self.assertIn("mode=exclusive", lock_string)
        self.assertIn("%x" % id(lock), lock_string)


@expandPermutations
class VolumeManifestTest(VdsmTestCase):

    def test_operation(self):
        img_id = make_uuid()
        vol_id = make_uuid()

        with fake_env('file') as env:
            env.make_volume(MB, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            vol.setMetadata = CountedInstanceMethod(vol.setMetadata)
            self.assertEqual(sc.LEGAL_VOL, vol.getLegality())
            with vol.operation():
                self.assertEqual(sc.ILLEGAL_VOL, vol.getLegality())
                self.assertEqual(1, vol.setMetadata.nr_calls)
            self.assertEqual(sc.LEGAL_VOL, vol.getLegality())
            self.assertEqual(2, vol.setMetadata.nr_calls)

    def test_operation_fail_inside_context(self):
        img_id = make_uuid()
        vol_id = make_uuid()

        with fake_env('file') as env:
            env.make_volume(MB, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            self.assertEqual(sc.LEGAL_VOL, vol.getLegality())
            with self.assertRaises(ValueError):
                with vol.operation():
                    raise ValueError()
            self.assertEqual(sc.ILLEGAL_VOL, vol.getLegality())

    @permutations(((None, 0), (100, 100)))
    def test_get_info_generation_id(self, orig_gen, info_gen):
        img_id = make_uuid()
        vol_id = make_uuid()

        with fake_env('file') as env:
            env.make_volume(MB, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            vol.getLeaseStatus = lambda: 'unused'
            if orig_gen is not None:
                vol.setMetaParam(sc.GENERATION, orig_gen)
            self.assertEqual(info_gen, vol.getInfo()['generation'])

    def test_operation_valid_generation(self):
        img_id = make_uuid()
        vol_id = make_uuid()
        generation = 100

        with fake_env('file') as env:
            env.make_volume(MB, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            vol.setMetaParam(sc.GENERATION, 100)
            with vol.operation(generation):
                pass
            self.assertEqual(generation + 1, vol.getMetaParam(sc.GENERATION))

    @permutations(((100, 99), (100, 101)))
    def test_operation_invalid_generation_raises(self, actual_generation,
                                                 requested_generation):
        img_id = make_uuid()
        vol_id = make_uuid()

        with fake_env('file') as env:
            env.make_volume(MB, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            vol.setMetaParam(sc.GENERATION, actual_generation)
            with self.assertRaises(se.GenerationMismatch):
                with vol.operation(requested_generation):
                    pass
            self.assertEqual(actual_generation,
                             vol.getMetaParam(sc.GENERATION))

    @permutations((
        (sc.MAX_GENERATION, 0),
        (sc.MAX_GENERATION - 1, sc.MAX_GENERATION),
    ))
    def test_generation_wrapping(self, first_gen, next_gen):
        img_id = make_uuid()
        vol_id = make_uuid()

        with fake_env('file') as env:
            env.make_volume(MB, img_id, vol_id)
            vol = env.sd_manifest.produceVolume(img_id, vol_id)
            vol.setMetaParam(sc.GENERATION, first_gen)
            with vol.operation(first_gen):
                pass
            self.assertEqual(next_gen, vol.getMetaParam(sc.GENERATION))


class CountedInstanceMethod(object):
    def __init__(self, method):
        self._method = method
        self.nr_calls = 0

    def __call__(self, *args, **kwargs):
        self.nr_calls += 1
        return self._method(*args, **kwargs)
