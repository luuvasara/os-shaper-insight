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
 * Class CompiledController, the generated and loaded shaper configuration as text panels
 * @package OPNsense\ShaperInsight
 */
class CompiledController extends ApiControllerBase
{
    /**
     * The API escapes <, > and & in JSON sent to a browser session (Response::send(),
     * htmlspecialchars ENT_NOQUOTES) but not to key-authenticated clients. "_probe" lets the
     * page tell the two apart and undo it once, so text is escaped exactly once on screen
     * and copied unescaped.
     */
    private const PROBE = ['_probe' => '<'];

    public function showAction()
    {
        $result = json_decode((new Backend())->configdRun('shaperinsight compiled'), true);
        return (is_array($result) ? $result : ['status' => 'failed']) + self::PROBE;
    }
}
