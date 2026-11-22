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

/**
 * refresh the addresses of the priority devices every minute: a device picked by its MAC
 * address keeps its priority when DHCP or SLAAC gives it a new address. Only while a priority
 * is enabled: otherwise the job would only add a configd log line per minute (review L4).
 * Priorities "Apply" and the package scripts run "configctl cron restart", so the job appears
 * and goes away without a reboot (review M7).
 */
function shaperinsight_cron()
{
    $jobs = [];
    if (file_exists('/var/run/shaperinsight-prio.removing')) {
        /* the plugin is being removed: prio.py purge set the flag (review S4) */
        return $jobs;
    }
    $cfg = \OPNsense\Core\Config::getInstance()->object();
    $wanted = false;
    if (isset($cfg->OPNsense->ShaperInsight->priorities->priority)) {
        foreach ($cfg->OPNsense->ShaperInsight->priorities->priority as $item) {
            if ((string)$item->enabled === '1') {
                $wanted = true;
                break;
            }
        }
    }
    if ($wanted) {
        $jobs[]['autocron'] = ['/usr/local/sbin/configctl -d shaperinsight prio refresh', '*'];
    }
    return $jobs;
}

/**
 * the history sampler as a service (System: Diagnostics: Services, the dashboard widget): start,
 * stop and restart through configd, alive while daemon(8) holds its pidfile
 */
function shaperinsight_services()
{
    $services = [];
    if (file_exists('/var/run/shaperinsight-prio.removing')) {
        return $services;
    }
    $cfg = \OPNsense\Core\Config::getInstance()->object();
    $general = $cfg->OPNsense->ShaperInsight->general ?? null;
    if ($general === null || (string)($general->sampler ?? '1') !== '0') {
        $services[] = [
            'description' => gettext('Shaper Insight history sampler'),
            'configd' => [
                'restart' => ['shaperinsight sampler restart'],
                'start' => ['shaperinsight sampler start'],
                'stop' => ['shaperinsight sampler stop'],
            ],
            'pidfile' => '/var/run/shaperinsight-sampler.pid',
            'name' => 'shaperinsight-sampler',
        ];
    }
    return $services;
}
