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

namespace OPNsense\ShaperInsight\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

/**
 * Class DevicesController, the devices and aliases the priority picker offers: current DHCP
 * leases (Kea and Dnsmasq, through core's own configd actions), static reservations of both,
 * and the firewall's host and network aliases. Read-only.
 * @package OPNsense\ShaperInsight
 */
class DevicesController extends ApiControllerBase
{
    private const PROBE = ['_probe' => '<'];

    /**
     * one row per IPv4 address; a reservation and a lease for the same address merge
     */
    private static function add(array &$rows, string $ip, string $name, string $mac, string $source, bool $static)
    {
        if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false) {
            return;
        }
        if (!isset($rows[$ip])) {
            $rows[$ip] = ['address' => $ip, 'hostname' => '', 'mac' => '', 'sources' => [], 'static' => false];
        }
        $row = &$rows[$ip];
        $row['hostname'] = $row['hostname'] !== '' ? $row['hostname'] : ($name === '*' ? '' : $name);
        $row['mac'] = $row['mac'] !== '' ? $row['mac'] : strtolower($mac);
        if (!in_array($source, $row['sources'])) {
            $row['sources'][] = $source;
        }
        /* only a reservation keeps the address: a dynamic lease may move to another host */
        $row['static'] = $row['static'] || $static;
    }

    /**
     * local devices seen with several addresses right now (CAKE counts each address as a host).
     * Here and not under status/: it lists MACs with all their addresses, the same data core keeps
     * behind Diagnostics: ARP/NDP, so the read-only status role must not get it (review S6)
     */
    public function sharesAction()
    {
        $result = json_decode((new Backend())->configdRun('shaperinsight prio shares'), true);
        return (is_array($result) ? $result : ['status' => 'failed', 'message' => 'configd did not answer'])
            + self::PROBE;
    }

    public function listAction()
    {
        $rows = [];
        $backend = new Backend();
        foreach (['kea list leases4' => 'Kea lease', 'dnsmasq list leases' => 'Dnsmasq lease'] as $action => $src) {
            $leases = json_decode($backend->configdRun($action), true);
            foreach ((is_array($leases) ? ($leases['records'] ?? []) : []) as $lease) {
                self::add(
                    $rows,
                    (string)($lease['address'] ?? ''),
                    (string)($lease['hostname'] ?? ''),
                    (string)($lease['hwaddr'] ?? ''),
                    $src,
                    false
                );
            }
        }
        $cfg = Config::getInstance()->object();
        if (isset($cfg->OPNsense->dnsmasq->hosts)) {
            foreach ($cfg->OPNsense->dnsmasq->hosts as $host) {
                /* ip may hold several addresses, comma separated */
                foreach (explode(',', (string)$host->ip) as $ip) {
                    self::add(
                        $rows,
                        trim($ip),
                        (string)$host->host,
                        explode(',', (string)$host->hwaddr)[0],
                        'Dnsmasq host',
                        true
                    );
                }
            }
        }
        if (isset($cfg->OPNsense->Kea->dhcp4->reservations->reservation)) {
            foreach ($cfg->OPNsense->Kea->dhcp4->reservations->reservation as $res) {
                self::add(
                    $rows,
                    (string)$res->ip_address,
                    (string)$res->hostname,
                    (string)$res->hw_address,
                    'Kea reservation',
                    true
                );
            }
        }
        /* Unbound host overrides name hosts that have no lease (static addresses, containers) */
        if (isset($cfg->OPNsense->unboundplus->hosts->host)) {
            foreach ($cfg->OPNsense->unboundplus->hosts->host as $host) {
                $named = (string)$host->rr === 'A' && (string)$host->hostname !== '';
                if ((string)$host->enabled !== '0' && $named) {
                    $ip = trim((string)$host->server);
                    self::add($rows, $ip, (string)$host->hostname, '', 'Unbound override', true);
                }
            }
        }
        $aliases = [];
        if (isset($cfg->OPNsense->Firewall->Alias->aliases->alias)) {
            foreach ($cfg->OPNsense->Firewall->Alias->aliases->alias as $alias) {
                if ((string)$alias->enabled === '1' && in_array((string)$alias->type, ['host', 'network'])) {
                    $aliases[] = ['name' => (string)$alias->name, 'type' => (string)$alias->type,
                        'description' => (string)$alias->description,
                        'content' => str_replace("\n", ', ', (string)$alias->content)];
                }
            }
        }
        $rows = array_values($rows);
        usort($rows, function ($a, $b) {
            return ip2long($a['address']) <=> ip2long($b['address']);
        });
        usort($aliases, function ($a, $b) {
            return strcasecmp($a['name'], $b['name']);
        });
        return ['devices' => $rows, 'aliases' => $aliases] + self::PROBE;
    }
}
