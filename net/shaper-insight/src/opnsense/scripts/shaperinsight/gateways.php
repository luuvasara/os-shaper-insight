#!/usr/local/bin/php
<?php

/*
 * Copyright (C) 2026 os-shaper-insight contributors
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

/*
 * The gateways as core sees them at runtime, for link detection (read-only). Dynamic gateways
 * (DHCP, PPPoE, DHCPv6: "WAN_DHCP", "WAN_DHCP6", the PPPoE peer) exist only here, never in
 * config.xml, and "upstream" is the gateway form's "Upstream gateway" flag (defaultgw).
 */

require_once('script/load_phalcon.php');

use OPNsense\Routing\Gateways;

$out = [];
/* with disabled and inactive ones (flagged), without the localhost Null4/Null6 */
foreach ((new Gateways())->gatewaysIndexedByName(true, false, true) as $name => $gw) {
    $out[] = [
        'name' => (string)$name,
        'interface' => (string)($gw['interface'] ?? ''),
        'if' => (string)($gw['if'] ?? ''),
        'ipprotocol' => (string)($gw['ipprotocol'] ?? ''),
        'upstream' => !empty($gw['defaultgw']) && (string)$gw['defaultgw'] !== '0',
        'dynamic' => !empty($gw['dynamic']),
        'disabled' => !empty($gw['disabled']) && (string)$gw['disabled'] !== '0',
    ];
}
echo json_encode($out) . "\n";
