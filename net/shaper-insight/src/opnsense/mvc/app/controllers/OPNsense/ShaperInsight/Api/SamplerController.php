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

/**
 * Class SamplerController, the history sampler and what it recorded. Full privilege only: the
 * history lists local addresses with their traffic (as Diagnostics: pfTop does)
 * @package OPNsense\ShaperInsight
 */
class SamplerController extends ApiControllerBase
{
    private const PROBE = ['_probe' => '<'];

    private function call(string $action, array $params = [])
    {
        $result = json_decode((new Backend())->configdpRun('shaperinsight sampler ' . $action, $params), true);
        $result = is_array($result) ? $result : ['status' => 'failed', 'message' => 'configd did not answer'];
        return $result + self::PROBE;
    }

    public function statusAction()
    {
        return $this->call('status');
    }

    /**
     * per address: MAC, manufacturer (core's IEEE list) and the other addresses on that MAC, for the
     * host tables; per-host data, so the same privilege as the history
     */
    public function hostsAction()
    {
        return $this->call('hosts');
    }

    /**
     * one pipe's hosts in the newest step: searched, sorted and paged server side (the table's group
     * rows, fetched only while open or searched; host simulation in TESTING.md)
     * @param string $pipe pipe number
     */
    public function newestAction($pipe = '')
    {
        $pipe = ctype_digit((string)$pipe) ? (string)$pipe : '';
        $q = (string)$this->request->get('q', null, '');
        $q = preg_match('/^[0-9A-Fa-f:.]{0,64}$/', $q) ? $q : '';
        $sort = $this->request->get('sort', null, 'rate') === 'ip' ? 'ip' : 'rate';
        $offset = (string)$this->request->get('offset', null, '0');
        $limit = (string)$this->request->get('limit', null, '50');
        return $this->call('newest', [$pipe, $q === '' ? '-' : $q, $sort,
            ctype_digit($offset) ? $offset : '0', ctype_digit($limit) ? $limit : '50']);
    }

    /**
     * @param string $seconds window, 60 s to one year
     */
    public function historyAction($seconds = '3600')
    {
        $seconds = ctype_digit((string)$seconds) ? (int)$seconds : 3600;
        return $this->call('history', [(string)max(60, min($seconds, 365 * 86400))]);
    }

    public function startAction()
    {
        return $this->request->isPost() ? $this->call('start') : ['status' => 'failed'];
    }

    public function stopAction()
    {
        return $this->request->isPost() ? $this->call('stop') : ['status' => 'failed'];
    }

    public function restartAction()
    {
        return $this->request->isPost() ? $this->call('restart') : ['status' => 'failed'];
    }
}
