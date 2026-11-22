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

"""Glossary builder: entries, groups, multi-line fields, mix-ups, URL resolution."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools', 'glossary'))
import build  # noqa: E402

MD = """# x
## Part 1. Glossary

### G1. Rate words

**t:shaping**
- Say: shaping delays packets;
  the queue holds them.
- People say: "throttling".
- Source: RFC 2475 (https://www.rfc-editor.org/rfc/rfc2475.txt): "Shapers delay"

**t:ecn** (RFC 3168) versus **t:l4s** (RFC 9330)
- Say: a flag.
- Source (secondary): RFC 2475: "same source"

## Part 2. Mix-ups

| # | People say | Say instead | Entry |
|---|---|---|---|
| 1 | "QoS is faster" | it decides who waits | t:shaping, t:ecn |
"""


class BuildTest(unittest.TestCase):
    def test_parse(self):
        groups, terms, mixups = build.parse(MD)
        self.assertEqual([g['id'] for g in groups], ['G1'])
        self.assertEqual(terms['shaping']['say'], 'shaping delays packets; the queue holds them.')
        self.assertEqual(terms['ecn']['note'], '(RFC 3168) versus L4S (RFC 9330)')
        self.assertEqual(terms['ecn']['source_note'], 'secondary')
        self.assertEqual(terms['ecn']['urls'], ['https://www.rfc-editor.org/rfc/rfc2475.txt'])
        self.assertEqual(mixups[0]['terms'], ['shaping', 'ecn'])


if __name__ == '__main__':
    unittest.main()
