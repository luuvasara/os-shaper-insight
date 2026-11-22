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

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

/**
 * Class PrioritiesController, the device priorities (grid) and their ipfw marks
 * @package OPNsense\ShaperInsight
 */
class PrioritiesController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'shaperinsight';
    protected static $internalModelClass = 'OPNsense\ShaperInsight\ShaperInsight';
    private const PROBE = ['_probe' => '<'];

    public function searchItemAction()
    {
        return $this->searchBase('priorities.priority', ['enabled', 'target', 'class', 'description'], 'target');
    }

    public function getItemAction($uuid = null)
    {
        return $this->getBase('priority', 'priorities.priority', $uuid);
    }

    /**
     * a typed alias target must be an enabled host or network alias, as the picker offers: a
     * URL table or a bogon list can hold 100 000 entries, and every address costs table work on
     * each ipfw reload (review S2; prio.py also refuses more than 1024 addresses per target)
     * @return string|null the problem, or null when the target is fine
     */
    private function targetProblem()
    {
        $target = trim((string)($this->request->getPost('priority')['target'] ?? ''));
        if (preg_match('/^(tcp|udp|tcp\+udp)\/([0-9]{1,5})(-([0-9]{1,5}))?$/', $target, $m)) {
            $lo = (int)$m[2];
            $hi = isset($m[4]) ? (int)$m[4] : $lo;
            return ($lo >= 1 && $hi <= 65535 && $lo <= $hi) ? null :
                'Ports go from 1 to 65535, and a range from low to high (ex. tcp+udp/6881-6889).';
        }
        if (
            $target === '' || filter_var(explode('/', $target)[0], FILTER_VALIDATE_IP) !== false ||
            preg_match('/^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/', $target)
        ) {
            return null;     /* addresses, networks and MACs: the model's own mask checks them */
        }
        $cfg = Config::getInstance()->object();
        if (isset($cfg->OPNsense->Firewall->Alias->aliases->alias)) {
            foreach ($cfg->OPNsense->Firewall->Alias->aliases->alias as $alias) {
                if ((string)$alias->name === $target) {
                    return in_array((string)$alias->type, ['host', 'network'], true) ? null :
                        sprintf(
                            'Alias %s is a "%s" alias; pick a host or network alias ' .
                            '(large lists such as URL tables cannot get a priority).',
                            $target,
                            (string)$alias->type
                        );
                }
            }
        }
        return sprintf('No host or network alias named %s.', $target);
    }

    private function refused($msg)
    {
        return ['result' => 'failed', 'validations' => ['priority.target' => $msg]];
    }

    public function addItemAction()
    {
        $msg = $this->request->isPost() ? $this->targetProblem() : null;
        return $msg === null ? $this->addBase('priority', 'priorities.priority') : $this->refused($msg);
    }

    public function setItemAction($uuid)
    {
        $msg = $this->request->isPost() ? $this->targetProblem() : null;
        return $msg === null ? $this->setBase('priority', 'priorities.priority', $uuid) : $this->refused($msg);
    }

    public function delItemAction($uuid)
    {
        return $this->delBase('priorities.priority', $uuid);
    }

    public function toggleItemAction($uuid, $enabled = null)
    {
        return $this->toggleBase('priorities.priority', $uuid, $enabled);
    }

    /**
     * load the marks for the saved priorities: ipfw rules and tables only, no shaper reload,
     * so traffic is never unshaped for it
     */
    public function applyAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $backend = new Backend();
        $result = json_decode($backend->configdRun('shaperinsight prio load'), true);
        // the every-minute address refresh exists only while a priority is enabled
        $backend->configdRun('cron restart');
        return (is_array($result) ? $result : ['status' => 'failed',
            'message' => 'the marks did not load; the status below shows why']) + self::PROBE;
    }

    /**
     * classes, resolved addresses per entry, the links marked and the rule counters
     */
    public function statusAction()
    {
        $result = json_decode((new Backend())->configdRun('shaperinsight prio status'), true);
        return (is_array($result) ? $result : ['status' => 'failed']) + self::PROBE;
    }
}
