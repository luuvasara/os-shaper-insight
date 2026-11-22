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

/**
 * Class GlossaryController, glossary terms and evidence entries for the term chips and "Why?" links
 * @package OPNsense\ShaperInsight
 */
class GlossaryController extends ApiControllerBase
{
    /**
     * The API escapes <, > and & in JSON sent to a browser session (Response::send(),
     * htmlspecialchars ENT_NOQUOTES) but not to key-authenticated clients. "_probe" lets the
     * page tell the two apart and undo it once, so text is escaped exactly once on screen
     * and copied unescaped.
     */
    private const PROBE = ['_probe' => '<'];

    private function load($name)
    {
        $path = __DIR__ . '/../../../../library/OPNsense/ShaperInsight/' . $name . '.json';
        $data = is_readable($path) ? json_decode(file_get_contents($path), true) : null;
        return is_array($data) ? $data : [];
    }

    /**
     * terms (glossary.json) and claims (claims.json) in one response, both are small
     */
    public function getAction()
    {
        return ['terms' => $this->load('glossary'), 'claims' => $this->load('claims')] + self::PROBE;
    }
}
