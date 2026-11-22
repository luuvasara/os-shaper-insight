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
 * Class ApplyController, apply the plan with a server-side confirm timer
 * @package OPNsense\ShaperInsight
 */
class ApplyController extends ApiControllerBase
{
    private const PROBE = ['_probe' => '<'];

    private function digits($name)
    {
        $v = (string)$this->request->getPost($name, null, '0');
        return ctype_digit($v) && strlen($v) <= 6 ? $v : '0';
    }

    private function run($action, $params)
    {
        $result = json_decode((new Backend())->configdpRun('shaperinsight ' . $action, $params), true);
        $result = is_array($result) ? $result : ['status' => 'failed', 'message' => 'configd did not answer'];
        return $result + self::PROBE;
    }

    /**
     * start: restore point, write, reconfigure, verify, then a 60 s countdown (POST down, up, conflicts, digest)
     */
    public function startAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $mode = (string)$this->request->getPost('conflicts', null, 'skip');
        $mode = in_array($mode, ['disable', 'disable-bulk'], true) ? $mode : 'skip';
        $digest = (string)$this->request->getPost('digest', null, '');
        if (!preg_match('/^[0-9a-f]{64}$/', $digest)) {
            return ['status' => 'failed', 'message' => 'preview the plan first'] + self::PROBE;
        }
        return $this->run('apply start', [$this->digits('down'), $this->digits('up'), $mode, $digest]);
    }

    /**
     * keep the change (POST job, the id the status shows); "no timer found" when nothing is waiting
     */
    public function confirmAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $job = (string)$this->request->getPost('job', null, '');
        if (!preg_match('/^[0-9a-f]{16}$/', $job)) {
            return ['status' => 'failed', 'message' => 'no timer found'] + self::PROBE;
        }
        return $this->run('apply confirm', [$job]);
    }

    /**
     * remove every object Shaper Insight created, switch rules it disabled back on (POST)
     */
    public function removeAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        return $this->run('apply remove', []);
    }

    /**
     * the admin's answer when a stopped apply needs a decision (POST choice=restore|keep)
     */
    public function resolveAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        $choice = (string)$this->request->getPost('choice', null, '');
        if (!in_array($choice, ['restore', 'keep'], true)) {
            return ['status' => 'failed', 'message' => 'choose restore or keep'] + self::PROBE;
        }
        return $this->run('apply resolve', [$choice]);
    }

    /**
     * a stopped job: put back what is still ours (POST; status only reports it, review S5)
     */
    public function recoverAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }
        return $this->run('apply recover', []);
    }

    /**
     * state, steps and seconds left; any tab can show the countdown. Read-only: a stopped job is
     * reported as recovery_pending, never recovered by a GET
     */
    public function statusAction()
    {
        return $this->run('apply status', []);
    }
}
