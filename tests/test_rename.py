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

"""The rename to os-shaper-insight (2026-11-22): objects made under the old name stay ours, the
migration from os-shaper-assistant is wired into the install and runs once, the menu says what it is."""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, '..', 'net', 'shaper-insight')
SCRIPTS = os.path.join(PKG, 'src', 'opnsense', 'scripts', 'shaperinsight')
sys.path.insert(0, SCRIPTS)
from lib.plan import build, norm  # noqa: E402

DET = {'links': [{'name': 'Link A', 'root': 'igb0', 'members': ['wan'], 'why': 'x'}], 'interfaces': []}
SNAP = {'config': {'pipes': [], 'queues': [], 'rules': []}, 'oscake': {},
        'kernel': {'dnctl': {'pipes': {}, 'queues': {}}, 'sysctl': {}}}


def read(*p):
    with open(os.path.join(*p)) as f:
        return f.read()


class LegacyObjects(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(norm('Assistant download Link A'), 'Insight download Link A')
        self.assertEqual(norm('[Assistant] Assistant upload Link A'), '[Insight] Insight upload Link A')
        self.assertEqual(norm('my pipe'), 'my pipe')

    def test_old_objects_are_changed_in_place_not_duplicated(self):
        first = build(DET, SNAP, 100, 20)
        snap = copy.deepcopy(SNAP)
        for i, o in enumerate(first['links'][0]['objects']):
            m = dict(o['model'], uuid='u%d' % i)
            # as os-shaper-assistant wrote them
            if o['kind'] == 'cake':
                m['description'] = m['description'].replace('[Insight] Insight', '[Assistant] Assistant')
                m['pipe'] = 'u%d' % [j for j, x in enumerate(first['links'][0]['objects'])
                                     if x.get('ref') == o['pipe_ref']][0]
            else:
                m['description'] = m['description'].replace('Insight ', 'Assistant ', 1)
                m['origin'] = 'ShaperAssistant'
            snap['config'].setdefault({'pipe': 'pipes', 'queue': 'queues', 'rule': 'rules',
                                       'cake': 'cake'}[o['kind']], []).append(m)
        again = build(DET, snap, 100, 20)
        objs = again['links'][0]['objects']
        self.assertEqual({o['action'] for o in objs}, {'change'})          # renamed in place
        self.assertEqual(again['remove'], [])                               # nothing orphaned
        self.assertTrue(all(o.get('uuid') for o in objs))                   # no new object
        self.assertTrue(all(o['model'].get('origin', 'ShaperInsight') == 'ShaperInsight' for o in objs))

    def test_writer_accepts_old_origin(self):
        src = read(SCRIPTS, 'writer.php')
        self.assertIn("const OURS = ['ShaperInsight', 'ShaperAssistant'];", src)
        self.assertIn("strpos($descr, '[Assistant] ') === 0", src)


class Migration(unittest.TestCase):
    def test_install_moves_files_and_runs_migrate_once(self):
        post = read(PKG, '+POST_INSTALL.post')
        self.assertIn('/conf/shaperassistant-${f}.json', post)
        self.assertIn('[ ! -e "/conf/shaperinsight-${f}.json" ]', post)       # never over a newer file
        self.assertIn("s#/conf/shaperassistant-#/conf/shaperinsight-#g", post)
        self.assertIn('/usr/local/opnsense/scripts/shaperinsight/migrate.php', post)
        mig = read(SCRIPTS, 'migrate.php')
        self.assertIn('unset($root->OPNsense->ShaperAssistant[0]);', mig)    # what makes it run once
        self.assertIn("if (!isset($cfg->object()->OPNsense->ShaperAssistant))", mig)
        self.assertIn('syslog(LOG_NOTICE', mig)
        self.assertTrue(os.access(os.path.join(SCRIPTS, 'migrate.php'), os.X_OK))


class Naming(unittest.TestCase):
    def test_menu_and_readme_say_what_it_is(self):
        menu = read(PKG, 'src', 'opnsense', 'mvc', 'app', 'models', 'OPNsense', 'ShaperInsight', 'Menu', 'Menu.xml')
        self.assertNotIn('Assistant', menu)
        self.assertEqual(menu.count('VisibleName="Shaper Insight: '), 7)
        readme = read(HERE, '..', 'README.md')
        self.assertIn('not related to OPNsense\'s\n**Reporting › Insight**', readme)
        self.assertIn('pkg delete -y os-shaper-assistant-devel', readme)


if __name__ == '__main__':
    unittest.main()
