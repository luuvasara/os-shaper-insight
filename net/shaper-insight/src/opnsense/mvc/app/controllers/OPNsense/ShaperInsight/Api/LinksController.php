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
use OPNsense\Core\Config;
use OPNsense\ShaperInsight\ShaperInsight;

/**
 * Class LinksController, the setup page's saved choices: which detected interfaces are internet
 * links (only where the user overrides detection) and each link's own speeds and line type
 * @package OPNsense\ShaperInsight
 */
class LinksController extends ApiControllerBase
{
    /**
     * the saved choices
     */
    public function getAction()
    {
        $mdl = new ShaperInsight();
        $out = ['choices' => [], 'links' => []];
        foreach ($mdl->links->choice->iterateItems() as $c) {
            $out['choices'][] = ['interface' => (string)$c->interface, 'include' => (string)$c->include];
        }
        foreach ($mdl->links->link->iterateItems() as $l) {
            $out['links'][] = ['port' => (string)$l->port, 'download' => (string)$l->download,
                'upload' => (string)$l->upload, 'line' => (string)$l->line];
        }
        return $out;
    }

    /**
     * save (POST shown[]: the interface keys the page lists; choices[]: {interface, include
     * yes|no}, the overrides among them; links[]:
     * {port, download, upload, line}, merged by port so a link unticked for a while keeps its
     * speeds). Nothing is applied: the page previews the plan with these afterwards.
     */
    public function setAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed', 'message' => 'POST only'];
        }
        $choices = $this->request->getPost('choices');
        $links = $this->request->getPost('links');
        $shown = $this->request->getPost('shown');
        $choices = is_array($choices) ? array_slice($choices, 0, 64) : [];
        $links = is_array($links) ? array_slice($links, 0, 32) : [];
        /* the interface keys the page listed: only their choices are replaced. A choice for an
           interface not listed now (disabled for maintenance) stays, so it still holds when the
           interface comes back (review S14) */
        $shown = is_array($shown) ? array_map('strval', array_slice($shown, 0, 64)) : [];
        foreach ($choices as $c) {
            if (is_array($c)) {
                $shown[] = (string)($c['interface'] ?? '');
            }
        }
        Config::getInstance()->lock();
        $mdl = new ShaperInsight();
        foreach (iterator_to_array($mdl->links->choice->iterateItems()) as $uuid => $c) {
            if (in_array((string)$c->interface, $shown, true)) {
                $mdl->links->choice->del($uuid);
            }
        }
        foreach ($choices as $c) {
            if (is_array($c)) {
                $mdl->links->choice->Add()->setNodes([
                    'interface' => (string)($c['interface'] ?? ''), 'include' => (string)($c['include'] ?? '')]);
            }
        }
        $byPort = [];
        foreach ($mdl->links->link->iterateItems() as $uuid => $l) {
            $byPort[(string)$l->port] = $l;
        }
        foreach ($links as $l) {
            if (!is_array($l)) {
                continue;
            }
            $port = (string)($l['port'] ?? '');
            $node = $byPort[$port] ?? $mdl->links->link->Add();
            $node->setNodes(['port' => $port, 'download' => (string)($l['download'] ?? '0'),
                'upload' => (string)($l['upload'] ?? '0'), 'line' => (string)($l['line'] ?? 'auto')]);
            $byPort[$port] = $node;
        }
        $errors = [];
        foreach ($mdl->performValidation() as $msg) {
            $errors[] = (string)$msg->getMessage();
        }
        if ($errors) {
            Config::getInstance()->unlock();
            return ['status' => 'failed', 'message' => implode(' ', array_unique($errors))];
        }
        $mdl->serializeToConfig();
        Config::getInstance()->save();
        return ['status' => 'ok'];
    }
}
