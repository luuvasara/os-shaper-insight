# Copyright (C) 2026 os-shaper-insight contributors
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Per-host bytes under drops (lab results in TESTING.md, per-host counting): max-min fair
water-filling over the pipe, per tin when the host's tin is known, instead of shrinking every host
by the same factor."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'net', 'shaper-insight', 'src', 'opnsense', 'scripts', 'shaperinsight')
sys.path.insert(0, SCRIPTS)
from lib.fairshare import water_fill, proportional, allocate, two_level, estimate  # noqa: E402


def close(tc, got, want, places=6):
    tc.assertEqual(set(got), set(want))
    for h in want:
        tc.assertAlmostEqual(got[h], want[h], places=places, msg=h)


class WaterFillTest(unittest.TestCase):
    def test_under_capacity_keeps_offers(self):
        close(self, water_fill({'a': 3, 'b': 4}, 10), {'a': 3, 'b': 4})

    def test_light_hosts_keep_offer_heavy_gets_rest(self):
        # 20 on the line, 3 light hosts at 2 each, one heavy offering 30: 2, 2, 2, 14
        close(self, water_fill({'h': 30, 'l1': 2, 'l2': 2, 'l3': 2}, 20), {'h': 14, 'l1': 2, 'l2': 2, 'l3': 2})

    def test_all_above_level_equal_split(self):
        close(self, water_fill({'u': 30, 'a': 6, 'b': 7, 'c': 8}, 20), {'u': 5, 'a': 5, 'b': 5, 'c': 5})

    def test_iterates(self):
        # level 25 after a (1) is fixed; then b (20) is under 33, fixed; c, d share 79 -> 39.5 each
        close(self, water_fill({'a': 1, 'b': 20, 'c': 50, 'd': 60}, 100), {'a': 1, 'b': 20, 'c': 39.5, 'd': 39.5})

    def test_sum_is_capacity_when_over(self):
        got = water_fill({'a': 11, 'b': 3, 'c': 27, 'd': 0.5, 'e': 9}, 30)
        self.assertAlmostEqual(sum(got.values()), 30)
        self.assertTrue(all(got[h] <= v + 1e-9 for h, v in {'a': 11, 'b': 3, 'c': 27, 'd': 0.5, 'e': 9}.items()))

    def test_zero_and_missing(self):
        close(self, water_fill({'a': 0, 'b': 10}, 5), {'a': 0, 'b': 5})
        close(self, water_fill({}, 5), {})
        close(self, water_fill({'a': 10}, 0), {'a': 0})
        close(self, water_fill({'a': 10}, None), {'a': 10})

    def test_weights_for_aggregate_row(self):
        # the summed rest '' stands for 3 hosts: its level share is 3x a single host's
        close(self, water_fill({'x': 40, '': 40}, 40, {'': 3}), {'x': 10, '': 30})


class TwoLevelTest(unittest.TestCase):
    def test_hosts_first_then_flows(self):
        # host a: 8 flows of 5 (40), host b: one flow of 3; line 20 -> b keeps 3, a gets 17 split 8 ways
        hosts, flows = two_level({'a': {i: 5 for i in range(8)}, 'b': {'f': 3}}, 20)
        close(self, hosts, {'a': 17, 'b': 3})
        close(self, flows['a'], {i: 17 / 8 for i in range(8)})
        close(self, flows['b'], {'f': 3})

    def test_host_totals_equal_one_level(self):
        f = {'a': {1: 9, 2: 1}, 'b': {1: 30}, 'c': {1: 2, 2: 2, 3: 2}}
        hosts, _ = two_level(f, 20)
        close(self, hosts, water_fill({h: sum(v.values()) for h, v in f.items()}, 20))

    def test_flows_inside_host_water_filled(self):
        _, flows = two_level({'a': {'small': 1, 'big': 30}}, 10)
        close(self, flows['a'], {'small': 1, 'big': 9})


class ProportionalTest(unittest.TestCase):
    def test_same_factor(self):
        close(self, proportional({'h': 30, 'l': 10}, 20), {'h': 15, 'l': 5})
        close(self, proportional({'h': 3, 'l': 1}, 20), {'h': 3, 'l': 1})


class AllocateTest(unittest.TestCase):
    def test_no_delivered(self):
        got, how = allocate({'a': 5}, None)
        self.assertEqual((got, how), ({'a': 5.0}, 'none'))

    def test_not_full(self):
        got, how = allocate({'a': 5, 'b': 4}, 10)
        self.assertEqual(how, 'none')
        close(self, got, {'a': 5, 'b': 4})

    def test_pipe_water_filling_without_tins(self):
        got, how = allocate({'h': 30, 'l1': 2, 'l2': 2, 'l3': 2}, 20)
        self.assertEqual(how, 'pipe')
        close(self, got, {'h': 14, 'l1': 2, 'l2': 2, 'l3': 2})

    def test_background_host_gets_bulk_tin(self):
        # diffserv4 order 0 BE, 1 Bulk, 2 Video, 3 Voice: the CS1 host sent 1.25, BE hosts 18.75
        tins = [18.75, 1.25, 0, 0]
        got, how = allocate({'bg': 20, 'a': 10, 'b': 10, 'c': 2}, 20, tins, {'bg': 1})
        self.assertEqual(how, 'tins')
        close(self, got, {'bg': 1.25, 'a': 8.375, 'b': 8.375, 'c': 2})

    def test_unmapped_tins_pool_into_default_group(self):
        # a host marks its own voice traffic (tin 3): nobody mapped there, so it is default capacity
        got, how = allocate({'bg': 5, 'a': 20, 'b': 20}, 20, [16, 1, 0, 3], {'bg': 1})
        self.assertEqual(how, 'tins')
        close(self, got, {'bg': 1, 'a': 9.5, 'b': 9.5})

    def test_mapped_tin_empty_falls_back_to_pipe(self):
        # marks not applied (besteffort entry, rules missing): Bulk sent nothing
        got, how = allocate({'bg': 20, 'a': 20}, 20, [20, 0, 0, 0], {'bg': 1})
        self.assertEqual(how, 'pipe')
        close(self, got, {'bg': 10, 'a': 10})

    def test_tins_not_matching_delivered_fall_back(self):
        got, how = allocate({'bg': 20, 'a': 20}, 20, [10, 1], {'bg': 1})
        self.assertEqual(how, 'pipe')

    def test_mapping_to_default_tin_is_default_group(self):
        got, how = allocate({'x': 20, 'a': 20}, 20, [20, 0, 0, 0], {'x': 0})
        self.assertEqual(how, 'pipe')
        close(self, got, {'x': 10, 'a': 10})

    def test_tin_group_under_capacity_keeps_offer(self):
        # misaligned: Bulk counted more than its host offered -> the host keeps its offer
        got, how = allocate({'bg': 1, 'a': 30}, 20, [18, 2, 0, 0], {'bg': 1})
        self.assertEqual(how, 'tins')
        close(self, got, {'bg': 1, 'a': 18})

    def test_strict_fills_group_to_capacity(self):
        got, how = allocate({'bg': 1, 'a': 30}, 20, [18, 2, 0, 0], {'bg': 1}, strict=True)
        self.assertEqual(how, 'tins')
        close(self, got, {'bg': 2, 'a': 18})
        got, how = allocate({'a': 6, 'b': 4}, 20, strict=True)
        self.assertEqual(how, 'pipe')
        close(self, got, {'a': 12, 'b': 8})


class EstimateTest(unittest.TestCase):
    """ 1.0.k: exact (LAN-side) bytes as they are, only the rest estimated (TESTING.md, per-host counting) """

    def test_no_pipe_total_is_counted(self):
        got, how = estimate({'a': 5, 'b': (9, 4, 6)}, None)
        close(self, got, {'a': 5, 'b': 7})
        self.assertEqual(how['a'], {'counted': 5})
        self.assertEqual(how['b'], {'exact': 4, 'counted': 3})

    def test_within_tolerance_nothing_corrected(self):
        # upload: the WAN-side count is after CAKE, 1 % over the pipe from the read timing
        got, how = estimate({'a': 60.6, 'b': 40}, 100, [100], after=True)
        close(self, got, {'a': 60.6, 'b': 40})
        self.assertEqual(set(how['a']), {'exact'})

    def test_before_cake_count_kept_is_checked_not_exact(self):
        # e-down: the firewall's own TCP download, pf 11.79 before CAKE, within the tolerance of the pipe
        got, how = estimate({'a': (13.5, 11.62, 13.5), 'fw': 11.79}, 23.0, [23.0])
        close(self, got, {'a': 11.62, 'fw': 11.79})
        self.assertEqual(how['fw'], {'checked': 11.79})
        self.assertEqual(how['a'], {'exact': 11.62})

    def test_exact_download_replaces_the_wan_count(self):
        # a-down: pf WAN counted 28.9 for the heavy host, the LAN side 24.9; the light ones lost nothing
        ent = {'h': (28.9, 24.9, 28.9), 'l1': (4.15, 4.14, 4.15), 'l2': (4.2, 4.2, 4.2)}
        got, how = estimate(ent, 33.3, [33.3])
        close(self, got, {'h': 24.9, 'l1': 4.14, 'l2': 4.2})
        self.assertEqual(set(how['h']), {'exact'})

    def test_rest_is_fair_share_inside_what_the_exact_hosts_left(self):
        # the firewall's own download has no LAN-side state: it gets what the pipe sent beyond the exact hosts
        ent = {'a': (30, 20, 28), 'fw': (10, 0, 0)}
        got, how = estimate(ent, 25, [25])
        close(self, got, {'a': 22, 'fw': 3})
        self.assertEqual(how['fw'], {'fair': 3})
        self.assertEqual(how['a'], {'exact': 20, 'fair': 2})

    def test_udp_flood_without_lan_state_fair_per_tin(self):
        # c-down without any LAN-side count: the flood does not take the TCP hosts' share
        got, how = estimate({'u': 61, 'a': 9.9, 'b': 9.9, 'c': 9.9}, 36, [36])
        close(self, got, {'u': 9, 'a': 9, 'b': 9, 'c': 9})
        self.assertEqual(set(how['u']), {'fair'})

    def test_background_host_gets_its_tin(self):
        got, how = estimate({'bg': 20, 'a': 10, 'b': 10, 'c': 2}, 20, [18.75, 1.25, 0, 0], {'bg': 1})
        close(self, got, {'bg': 1.25, 'a': 8.375, 'b': 8.375, 'c': 2})
        self.assertEqual(set(how['bg']), {'fair'})

    def test_tins_unknown_is_scaled_never_whole_pipe_fill(self):
        got, how = estimate({'h': 30, 'l': 10}, 20, None)
        close(self, got, {'h': 15, 'l': 5})
        self.assertEqual(set(how['h']), {'scaled'})

    def test_bytes_in_a_tin_no_host_is_known_to_use_is_scaled(self):
        # d-down with the Background host not in a priority table (it marks itself): Bulk carried 2.46 of
        # 40, which water-filling would hand to the normal hosts -> scaled instead
        got, how = estimate({'bg': 3.52, 'a': 20, 'b': 20}, 40, [37.54, 2.46, 0, 0])
        self.assertEqual({m for v in how.values() for m in v}, {'scaled'})
        self.assertAlmostEqual(sum(got.values()), 40)

    def test_unknown_tin_below_tolerance_still_fair(self):
        got, how = estimate({'a': 30, 'b': 2}, 20, [19.9, 0.1, 0, 0])
        close(self, got, {'a': 18, 'b': 2})
        self.assertEqual(set(how['a']), {'fair'})

    def test_mapped_tin_that_sent_nothing_is_scaled(self):
        got, how = estimate({'bg': 20, 'a': 20}, 20, [20, 0, 0, 0], {'bg': 1})
        close(self, got, {'bg': 10, 'a': 10})
        self.assertEqual(set(how['bg']), {'scaled'})

    def test_tins_not_matching_delivered_are_unknown(self):
        _, how = estimate({'bg': 20, 'a': 20}, 20, [10, 1], {'bg': 1})
        self.assertEqual(set(how['a']), {'scaled'})

    def test_exact_above_the_pipe_leaves_nothing_for_the_rest(self):
        # misaligned reads: the exact hosts already account for all the pipe sent
        got, _ = estimate({'a': (12, 10.5, 12), 'fw': 2}, 10, [10])
        close(self, got, {'a': 10.5, 'fw': 0})

    def test_never_more_than_counted(self):
        got, _ = estimate({'a': (12, 6, 8), 'fw': 1}, 20, [20])
        self.assertLessEqual(got['fw'], 1)
        self.assertAlmostEqual(got['a'], 10)

    def test_weights_for_the_summed_rest(self):
        got, _ = estimate({'x': 40, '': 40}, 40, [40], None, {'': 3})
        close(self, got, {'x': 10, '': 30})


if __name__ == '__main__':
    unittest.main()
