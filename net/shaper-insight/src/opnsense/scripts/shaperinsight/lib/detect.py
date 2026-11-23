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
    Internet-link (WAN) and local-network (LAN) detection with a plain-word reason for
    every signal, and the walk from each WAN down to its physical port, so that all
    WANs on one cable get one shaper. Read-only: config.xml, ifconfig -a,
    netstat -rn and core's runtime gateway list (one run each).

    Strong signs follow OPNsense's own (topology test 2026-11-22, F1/F5): a gateway marked
    "Upstream gateway", a dynamic gateway (DHCP, PPPoE, DHCPv6: they exist only at runtime),
    the default route, or an address from upstream. A plain gateway is only a "maybe": it may
    lead to a downstream router. Tunnels (WireGuard, OpenVPN, GRE, ...) are never links, even
    with a gateway. The user ticks or unticks each row; the choice is saved (selection).
"""
import ipaddress
import re

VPN_PREFIXES = ('wg', 'ovpnc', 'ovpns', 'tun', 'tap', 'zt', 'ipsec', 'enc', 'gif', 'gre')
SKIP_PREFIXES = ('lo', 'pflog', 'pfsync', 'enc')
CGNAT = ipaddress.ip_network('100.64.0.0/10')
DOC_NETS = [ipaddress.ip_network(n) for n in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32')]
WANISH = re.compile(r'^(wan.*|.*_?wans?|uplink.*|internet.*)$', re.I)


def parse_ifconfig(text):
    """ ifconfig -a -> {dev: {inet: [...], inet6: [...], groups: [...], vlan_parent, vlan_tag,
    laggports: [...], members: [...], up}} """
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r'^([a-z0-9_.]+): flags=\S*<([^>]*)>', line)
        if m:
            cur = out.setdefault(m.group(1), {'inet': [], 'inet6': [], 'groups': [], 'vlan_parent': None,
                                              'vlan_tag': None, 'laggports': [], 'members': [],
                                              'up': 'UP' in m.group(2).split(','), 'ipv6_disabled': False})
            continue
        if cur is None:
            continue
        s = line.strip()
        m = re.match(r'^inet (\S+)', s)
        if m:
            cur['inet'].append(m.group(1))
        m = re.match(r'^inet6 (\S+?)(%\S+)? ', s + ' ')
        if m:
            cur['inet6'].append(m.group(1))
        # "nd6 options=...<...,IFDISABLED,...>": IPv6 switched off on this interface
        m = re.match(r'^nd6 options=\S*<([^>]*)>', s)
        if m:
            cur['ipv6_disabled'] = 'IFDISABLED' in m.group(1).split(',')
        m = re.match(r'^groups: (.*)', s)
        if m:
            cur['groups'] = m.group(1).split()
        m = re.search(r'vlan: (\d+).*parent interface: (\S+)', s)
        if m:
            cur['vlan_tag'], cur['vlan_parent'] = int(m.group(1)), m.group(2)
        m = re.match(r'^laggport: (\S+)', s)
        if m:
            cur['laggports'].append(m.group(1))
        m = re.match(r'^member: (\S+)', s)
        if m:
            cur['members'].append(m.group(1))
    return out


def parse_routes(text):
    """ netstat -rn: the interfaces holding a default route, per family """
    out = {'inet': set(), 'inet6': set()}
    fam = None
    for line in text.splitlines():
        if line.startswith('Internet6'):
            fam = 'inet6'
        elif line.startswith('Internet'):
            fam = 'inet'
        parts = line.split()
        if fam and len(parts) >= 4 and parts[0] == 'default':
            out[fam].add(parts[3])
    return {k: sorted(v) for k, v in out.items()}


def _public(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    if ip.version == 4 and ip in CGNAT:
        return 'cgnat'
    # documentation ranges (RFC 5737/3849) count as public: test labs and examples use them
    # as their "internet" addresses
    if any(ip in n for n in DOC_NETS if n.version == ip.version):
        return 'public'
    # deprecated IPv6 site-local fec0::/10 (QEMU user networking hands it out) is not global,
    # but Python's is_global says so (review L8)
    if ip.version == 6 and ip.is_site_local:
        return 'private'
    return 'public' if ip.is_global else 'private'


def read_extra(root):
    """ the bits of config.xml detection needs that read_config() leaves out """
    vlans = {}
    for v in root.findall('./vlans/vlan'):
        vlans[v.findtext('vlanif') or ''] = {'parent': v.findtext('if') or '', 'tag': v.findtext('tag') or ''}
    ppps = {}
    for p in root.findall('./ppps/ppp'):
        ppps[p.findtext('if') or ''] = {'ports': (p.findtext('ports') or '').split(','), 'type': p.findtext('type') or ''}
    gws = []
    for path in ('./OPNsense/Gateways/gateway_item', './gateways/gateway_item'):
        for g in root.findall(path):
            gws.append({'name': g.findtext('name') or '', 'interface': g.findtext('interface') or '',
                        'disabled': (g.findtext('disabled') or '0') == '1', 'ipprotocol': g.findtext('ipprotocol') or '',
                        # "Upstream gateway" in the gateway form is stored as defaultgw
                        'upstream': (g.findtext('defaultgw') or '0') == '1', 'dynamic': False})
    # policy routing: enabled filter rules with a gateway (or a gateway group) set
    gw_groups = {}
    for grp in root.findall('./gateways/gateway_group'):
        items = []
        for it in grp.findall('item') + grp.findall('item2'):
            items += [x.split('|')[0] for x in (it.text or '').split(',') if x.strip()]
        gw_groups[grp.findtext('name') or ''] = items
    gw_rules = []
    for r in root.findall('./filter/rule'):
        if r.find('disabled') is None and (r.findtext('gateway') or '').strip():
            gw_rules.append({'descr': r.findtext('descr') or '', 'gateway': r.findtext('gateway').strip()})
    for r in root.findall('./OPNsense/Firewall/Filter/rules/rule'):
        if (r.findtext('enabled') or '1') == '1' and (r.findtext('gateway') or '').strip():
            gw_rules.append({'descr': r.findtext('description') or '', 'gateway': r.findtext('gateway').strip()})
    nat_ifs = set()
    for r in root.findall('./nat/outbound/rule'):
        if r.find('disabled') is None:
            nat_ifs.add(r.findtext('interface') or '')
    for r in root.findall('./OPNsense/Firewall/Filter/snatrules/rule'):
        if (r.findtext('enabled') or '1') == '1':
            nat_ifs.update((r.findtext('interface') or '').split(','))
    ifs = {}
    el = root.find('./interfaces')
    for i in (el if el is not None else []):
        ifs[i.tag] = {
            'if': i.findtext('if') or '', 'descr': i.findtext('descr') or i.tag.upper(),
            # core writes <enable>1</enable> for every enabled interface, wan and lan included;
            # a disabled one has no <enable> or an empty one (topology 6: a spare "wan" port)
            'enable': (i.findtext('enable') or '').strip() not in ('', '0'),
            'track6': i.findtext('track6-interface') or '',
            'ipaddr': i.findtext('ipaddr') or '', 'ipaddrv6': i.findtext('ipaddrv6') or '',
            'gateway': i.findtext('gateway') or '', 'gatewayv6': i.findtext('gatewayv6') or '',
            'virtual': i.find('virtual') is not None, 'type': i.findtext('type') or '',
        }
    return {'vlans': vlans, 'ppps': ppps, 'gateways': gws, 'nat_ifs': sorted(x for x in nat_ifs if x),
            'gw_groups': gw_groups, 'gw_rules': gw_rules,
            'interfaces': ifs}


def root_device(dev, extra, ifc, trail=None):
    """ walk VLAN and PPPoE parents down to the physical port; LAGG and bridges stop the walk
    :return: (root, [steps], note)
    """
    trail = trail or []
    if dev in trail or len(trail) > 8:
        return dev, trail, 'loop'
    trail = trail + [dev]
    vlan = extra['vlans'].get(dev) or ({'parent': ifc.get(dev, {}).get('vlan_parent')}
                                       if ifc.get(dev, {}).get('vlan_parent') else None)
    if vlan and vlan.get('parent'):
        return root_device(vlan['parent'], extra, ifc, trail)
    ppp = extra['ppps'].get(dev)
    if ppp and ppp['ports'] and ppp['ports'][0]:
        return root_device(ppp['ports'][0], extra, ifc, trail)
    if dev.startswith('lagg'):
        return dev, trail, 'lagg'
    if dev.startswith('bridge'):
        return dev, trail, 'bridge'
    return dev, trail, ''


def group_names(root):
    """ the interface groups (Firewall: Groups); core also lists some of them under <interfaces>
    ("WireGuard (Group)", device "wireguard"), but they are not interfaces """
    names = {g.findtext('ifname') or '' for g in root.findall('./ifgroups/ifgroupentry')}
    return {n for n in names if n} | {'wireguard', 'openvpn', 'ipsec', 'enc0'}


def detect(root, ifconfig_text, netstat_text, gateways=None, selection=None):
    """ :param gateways: core's runtime gateway list (scripts/shaperinsight/gateways.php):
        [{name, interface, upstream, dynamic, disabled}]; None = only what config.xml stores
    :param selection: {key: True/False} the user's ticks; overrides the detected role """
    extra = read_extra(root)
    ifc = parse_ifconfig(ifconfig_text)
    routes = parse_routes(netstat_text)
    selection = selection or {}
    groups_ = group_names(root)
    gw_by_if = {}
    for g in (gateways if gateways is not None else extra['gateways']):
        if not g.get('disabled'):
            gw_by_if.setdefault(g.get('interface', ''), []).append(g)
    rows = []
    for key, i in sorted(extra['interfaces'].items()):
        dev = i['if']
        if not dev or dev.startswith(SKIP_PREFIXES) or not i['enable'] or i['type'] == 'group' \
                or dev in groups_ or key in groups_:
            continue
        live = ifc.get(dev, {'inet': [], 'inet6': [], 'groups': [], 'up': False})
        sig = []   # (strength, text, true/false)
        gws = list(gw_by_if.get(key, []))
        names = {g['name'] for g in gws}
        gws += [{'name': x, 'upstream': False, 'dynamic': False} for x in (i['gateway'], i['gatewayv6'])
                if x and x not in names]
        upstream = [g['name'] for g in gws if g.get('upstream') or g.get('dynamic')]
        plain = [g['name'] for g in gws if not (g.get('upstream') or g.get('dynamic'))]
        if upstream:
            sig.append(('strong', 'Has upstream gateway %s (OPNsense marks it as facing the internet: '
                                  '"Upstream gateway" set, or DHCP/PPPoE/DHCPv6)' % ', '.join(upstream), True))
        if plain:
            sig.append(('medium', 'Has gateway %s, not marked "Upstream gateway" (it may lead to another '
                                  'local router)' % ', '.join(plain), True))
        if not gws:
            sig.append(('info', 'No gateway', False))
        fams = [f for f in ('inet', 'inet6') if dev in routes[f]]
        if fams:
            sig.append(('strong', 'Holds the %s default route' % ' and '.join('IPv4' if f == 'inet' else 'IPv6' for f in fams), True))
        client = [x for x in (i['ipaddr'], i['ipaddrv6']) if x in ('dhcp', 'pppoe', 'pptp', 'l2tp', 'dhcp6', 'slaac')]
        if client:
            sig.append(('strong', 'Gets its address as a %s client from upstream' % '/'.join(c.upper() for c in client), True))
        tracked = i['ipaddrv6'] in ('track6', 'idassoc6')
        if tracked:
            # a prefix delegated from the WAN and handed on: local by definition (F4)
            sig.append(('info', 'Uses an IPv6 prefix delegated through %s (a local network)'
                        % (extra['interfaces'].get(i['track6'], {}).get('descr') or i['track6'] or 'the WAN'), True))
        v6 = [] if tracked else [a for a in live['inet6'] if not a.lower().startswith('fe80')]
        kinds = {_public(a) for a in live['inet'] + v6}
        if 'public' in kinds:
            sig.append(('medium', 'Has a public address', True))
        elif 'cgnat' in kinds:
            sig.append(('medium', 'Carrier-grade NAT address (shared with other customers)', True))
        if key in extra['nat_ifs']:
            sig.append(('medium', 'Carries outbound NAT rules', True))
        if any(WANISH.match(g) for g in live['groups']):
            sig.append(('weak', 'Member of a WAN-like interface group', True))
        if WANISH.match(i['descr']) or key.startswith('wan'):
            sig.append(('weak', 'Named "%s"' % i['descr'], True))
        vpn = dev.startswith(VPN_PREFIXES)
        strong = any(s == 'strong' and t for s, _, t in sig)
        medium = any(s == 'medium' and t for s, _, t in sig)
        # strong sign: internet link, pre-selected. Only medium signs (outbound NAT, a public
        # address): shown as "maybe" and left for the user to tick, never guessed silently
        role = 'vpn' if vpn else ('wan' if strong else ('maybe' if medium else 'lan'))
        root_dev, trail, note = root_device(dev, extra, ifc)
        # the user's tick wins (F3); a tunnel is never shaped as a link of its own
        chosen = selection.get(key)
        selected = (role == 'wan') if chosen is None or vpn else bool(chosen)
        rows.append({'key': key, 'descr': i['descr'], 'device': dev, 'root': root_dev, 'path': trail,
                     'note': note, 'role': role, 'selected': selected, 'chosen': chosen is not None and not vpn,
                     'signals': [{'strength': s, 'text': t, 'true': ok} for s, t, ok in sig],
                     'vlan': any([dev in extra['vlans'], bool(ifc.get(dev, {}).get('vlan_parent')),
                                  any(x in extra['vlans'] for x in trail[1:])]),
                     'addresses': live['inet'] + live['inet6'], 'up': live.get('up', False),
                     # IPv6 off: nd6 IFDISABLED, or not even a link-local address (health S3)
                     'ipv6_off': dev in ifc and (live.get('ipv6_disabled', False) or not live['inet6']),
                     # a gateway on the interface: pf adds reply-to to its rules (health S3)
                     'gateway': bool(gws), 'vlan_parent': ifc.get(dev, {}).get('vlan_parent')})
    groups = {}
    for r in rows:
        if r['selected']:
            groups.setdefault(r['root'], []).append(r['key'])
    links = []
    for n, (rdev, members) in enumerate(sorted(groups.items())):
        name = 'Link %s' % chr(ord('A') + n) if n < 26 else 'Link %d' % (n + 1)
        why = ('%d internet links share the physical port %s, so they share one speed limit' % (len(members), rdev)
               if len(members) > 1 else 'one internet link on the physical port %s' % rdev)
        links.append({'name': name, 'root': rdev, 'members': members, 'why': why})
    # which interfaces the gateway rules send traffic out of (a group: every member; a gateway
    # nobody knows: '' so the plan stays careful)
    gw_if = {g['name']: g.get('interface', '') for g in
             (gateways if gateways is not None else extra['gateways'])}
    gw_rules = []
    for r in extra['gw_rules']:
        names = extra['gw_groups'].get(r['gateway'], [r['gateway']])
        gw_rules.append(dict(r, interfaces=sorted({gw_if.get(n, '') for n in names})))
    return {'interfaces': rows, 'links': links, 'routes': routes, 'gateway_rules': gw_rules}
