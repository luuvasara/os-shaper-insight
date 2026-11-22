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
 * Class HealthController, shaper health checks S1-S8 and internet-link detection
 * @package OPNsense\ShaperInsight
 */
class HealthController extends ApiControllerBase
{
    /**
     * The API escapes <, > and & in JSON sent to a browser session (Response::send(),
     * htmlspecialchars ENT_NOQUOTES) but not to key-authenticated clients. "_probe" lets the
     * page tell the two apart and undo it once, so text is escaped exactly once on screen
     * and copied unescaped.
     */
    private const PROBE = ['_probe' => '<'];

    /**
     * checks S1-S8 (not polled: the page runs it on load and on "Check again")
     */
    public function getAction()
    {
        $result = json_decode((new Backend())->configdRun('shaperinsight health'), true);
        return (is_array($result) ? $result : ['status' => 'failed']) + self::PROBE;
    }

    /**
     * internet/local link detection with the reasons, and the plan preview (dry run); each
     * link's speeds come from the saved choices (links/set), down/up are only the fallback
     */
    public function detectAction()
    {
        $down = (string)$this->request->get('down', null, '0');
        $up = (string)$this->request->get('up', null, '0');
        /* whole Mbit/s only; detect.py checks again */
        $down = ctype_digit($down) ? $down : '0';
        $up = ctype_digit($up) ? $up : '0';
        /* "Keep mine" (skip) or "Disable mine": the plan shows what Apply would really do */
        $mode = (string)$this->request->get('conflicts', null, 'skip');
        $mode = in_array($mode, ['disable', 'disable-bulk'], true) ? $mode : 'skip';
        $result = json_decode((new Backend())->configdpRun('shaperinsight detect', [$down, $up, $mode]), true);
        return (is_array($result) ? $result : ['status' => 'failed']) + self::PROBE;
    }
}
