"""
    Copyright (c) 2026 os-shaper-insight contributors
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.
    --------------------------------------------------------------------------------------
    Redaction for text that users paste into forum posts. It fails closed: the text is
    split into broad candidate tokens (Unicode letters, digits, '_' and . : % -), each
    token is classified, and what is not known to be harmless is replaced:

      public IPv4/IPv6 (incl. IPv4-mapped, zone ids, CIDR, v4:port)  -> <public-v4-N>, <public-v6-N>
      MAC addresses (aa:bb:.., aa-bb-.., aabb.ccdd.eeff, aabbccddeeff
      when it has a letter: twelve digits alone are a counter)       -> <mac-N>
      DUIDs and other colon/dash hex byte strings longer than a MAC  -> <duid-N>
      anything shaped like a dotted quad that is not a private
      address in canonical form (010.0.0.1, 300.1.1.1, a.b.c.d.port) -> <addr-N>
      every other token containing a dot whose last label starts
      with a letter (host names, also nas.home.conf, a_b.example,
      bücher.example)                                              -> <host-N>
      with descriptions=True: the free text after "#" or "//" on a
      line (object descriptions, ipfw comments: "Johns-laptop")    -> <descr-N>

    Kept: private addresses (RFC 1918, ULA, link-local, CGNAT, loopback; the documentation
    ranges are NOT kept, they are treated as public), counted in counts()['private_kept']
    so the page can say so; numbers and version strings (100.000, 26.7.9_3, 15.1-RELEASE:
    the last label starts with a digit, and no top-level domain does); and tokens that
    EXACTLY match the caller's allow-list (sysctl names read from the kernel, file names
    the caller itself emits). There is no prefix or suffix exemption: "net.example.org" and
    "nas.home.conf" are host names. The same value gets the same placeholder in every
    panel of one page.
"""
import ipaddress
import re

# a token starts with a word character, or with ':' before a hex digit or ':' (an IPv6
# address written "::2a01:..." would otherwise lose its leading "::" and pass unchanged)
TOKEN = re.compile(r'(?:[^\W]|:(?=[:0-9A-Fa-f]))[\w.:%-]*(?:/\d{1,3})?')
TRAIL = '.:-_%'
MAC_COLON = re.compile(r'[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4,}')
MAC_DOTTED = re.compile(r'[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}')
MAC_BARE = re.compile(r'(?=[0-9]*[A-Fa-f])[0-9A-Fa-f]{12}')
COMMENT = re.compile(r'(#|//)[ \t]*(\S.*?)[ \t]*$', re.M)
QUAD = re.compile(r'(?<!\d)(\d+(?:\.\d+){3})(?!\d)')
# an IPv6 address glued to other characters (x2a01:4f8::1): found inside a token (review S12)
EMBEDDED6 = re.compile(r'[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{0,4}){2,7}')
CGNAT = ipaddress.ip_network('100.64.0.0/10')
SYSCTL_NAME = re.compile(r'^([a-z][a-z0-9_]*(?:\.[a-z0-9_%]+)+)[=:]', re.M)


def sysctl_names(text):
    """ the names of `sysctl -e` (name=value) or `sysctl` (name: value) output lines; only
    for text read from the kernel, never for config or user text """
    return set(SYSCTL_NAME.findall(text or ''))


class Redactor:
    def __init__(self, allow=()):
        """ :param allow: exact tokens to keep (sysctl names, file names the caller emits) """
        self.allow = set(allow)
        self.maps = {'v4': {}, 'v6': {}, 'mac': {}, 'duid': {}, 'addr': {}, 'host': {}, 'descr': {}}
        self.private_kept = set()

    def _tag(self, kind, value, label):
        table = self.maps[kind]
        if value not in table:
            table[value] = '<%s-%d>' % (label, len(table) + 1)
        return table[value]

    def _ip(self, text):
        """ placeholder for a public address, the text itself for a private one,
        None when text is not an IP address """
        core, _, prefix = text.partition('/')
        try:
            ip = ipaddress.ip_address(core)
        except ValueError:
            return None
        check = ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip
        local = any([check.is_private, check.is_loopback, check.is_link_local, check.is_unspecified,
                     check.version == 4 and check in CGNAT])
        private = local and not _documentation(check)
        if private:
            self.private_kept.add(str(ip))
            return text
        kind, label = ('v4', 'public-v4') if ip.version == 4 else ('v6', 'public-v6')
        return self._tag(kind, str(ip).lower(), label) + ('/' + prefix if prefix else '')

    def _quad(self, m):
        """ a dotted quad inside a token: a canonical private address is kept, anything
        else (public, leading zeros, octet > 255) is replaced """
        q = m.group(1)
        out = self._ip(q)
        return out if out is not None else self._tag('addr', q, 'addr')

    def _token(self, tok):
        # MAC / DUID first: a colon MAC would also parse as nothing else, but a DUID
        # could be mistaken for an IPv6 fragment
        if MAC_BARE.fullmatch(tok):
            return self._tag('mac', tok.lower(), 'mac')
        if MAC_COLON.fullmatch(tok) or MAC_DOTTED.fullmatch(tok):
            octets = len(re.findall(r'[0-9A-Fa-f]{2}', tok.replace('.', '')))
            norm = re.sub(r'[^0-9a-f]', '', tok.lower())
            return self._tag('mac', norm, 'mac') if octets == 6 else self._tag('duid', norm, 'duid')
        out = self._ip(tok)
        if out is not None:
            return out
        # IPv4 with a port (203.0.113.5:443)
        if tok.count(':') == 1 and '.' in tok:
            host, port = tok.split(':')
            out = self._ip(host)
            if out is not None and port.isdigit():
                return out + ':' + port
        if tok in self.allow:
            return tok
        if ':' in tok and EMBEDDED6.search(tok):
            def emb(m):
                out = self._ip(m.group(0))
                return out if out is not None else m.group(0)
            new = EMBEDDED6.sub(emb, tok)
            if new != tok:
                return new
        if '.' not in tok:
            return tok
        if QUAD.search(tok):
            return QUAD.sub(self._quad, tok)
        last = tok.rsplit('.', 1)[1]
        if last[:1].isdigit():
            return tok      # a number or a version: 100.000, 26.7.9_3, 15.1-RELEASE-p6
        return self._tag('host', tok.lower(), 'host')

    def text(self, text, descriptions=False):
        if not text:
            return text
        if descriptions:
            # free text the admin typed (descriptions, comments) can name people and places;
            # it cannot be classified, so it is replaced as a whole
            text = COMMENT.sub(lambda m: m.group(1) + ' ' + self._tag('descr', m.group(2), 'descr'), text)

        def repl(m):
            tok = m.group(0)
            core = tok.rstrip(TRAIL)
            tail = tok[len(core):]
            if not core:
                return tok
            return self._token(core) + tail
        return TOKEN.sub(repl, text)

    def counts(self):
        out = {k: len(v) for k, v in self.maps.items()}
        out['private_kept'] = len(self.private_kept)
        return out


def _documentation(ip):
    """ RFC 5737 / RFC 3849 documentation ranges: never kept, so examples stay visible as redacted """
    nets = ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32')
    return any(ip in ipaddress.ip_network(n) for n in nets if ipaddress.ip_network(n).version == ip.version)
