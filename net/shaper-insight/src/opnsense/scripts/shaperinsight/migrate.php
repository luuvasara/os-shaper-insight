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
 * One-time migration from the plugin's earlier name, os-shaper-assistant (+POST_INSTALL).
 * Copies the settings, priorities and saved link choices of //OPNsense/ShaperAssistant into
 * //OPNsense/ShaperInsight, then deletes the old section, so it runs once: without the old
 * section it does nothing. The shaper sections are not touched (objects made under the old
 * name stay; apply and Remove still treat them as ours). Every step goes to the system log.
 */

require_once('script/load_phalcon.php');

use OPNsense\Core\Config;
use OPNsense\ShaperInsight\ShaperInsight;

$say = function ($msg) {
    syslog(LOG_NOTICE, 'shaperinsight: migration: ' . $msg);
    echo "Shaper Insight migration: {$msg}\n";
};

openlog('shaperinsight', LOG_ODELAY, LOG_USER);
$cfg = Config::getInstance();
if (!isset($cfg->object()->OPNsense->ShaperAssistant)) {
    exit(0);                    /* nothing from os-shaper-assistant: a fresh install */
}
$cfg->lock();
$old = $cfg->object()->OPNsense->ShaperAssistant;
$mdl = new ShaperInsight();
$copied = ['priorities' => 0, 'choices' => 0, 'links' => 0];
$plain = [
    'general' => ['redact', 'poll_seconds'],
    'bufferbloat' => ['download_url', 'upload_url', 'reflectors', 'streams', 'phase_seconds', 'cap_mbit', 'label'],
];
foreach ($plain as $section => $fields) {
    foreach ($fields as $f) {
        if (isset($old->$section->$f)) {
            $mdl->$section->$f = (string)$old->$section->$f;
        }
    }
}
if (isset($old->priorities->priority)) {
    foreach ($old->priorities->priority as $p) {
        $mdl->priorities->priority->Add()->setNodes([
            'enabled' => (string)$p->enabled,
            'target' => (string)$p->target,
            'class' => (string)$p->class,
            'description' => (string)$p->description,
        ]);
        $copied['priorities']++;
    }
}
if (isset($old->links->choice)) {
    foreach ($old->links->choice as $c) {
        $mdl->links->choice->Add()->setNodes(['interface' => (string)$c->interface, 'include' => (string)$c->include]);
        $copied['choices']++;
    }
}
if (isset($old->links->link)) {
    foreach ($old->links->link as $l) {
        $mdl->links->link->Add()->setNodes(['port' => (string)$l->port, 'download' => (string)$l->download,
            'upload' => (string)$l->upload, 'line' => (string)$l->line]);
        $copied['links']++;
    }
}
$errors = [];
foreach ($mdl->performValidation() as $msg) {
    $errors[] = $msg->getField() . ': ' . $msg->getMessage();
}
if ($errors) {
    /* keep the old section, so nothing is lost; the admin can enter the settings again */
    $cfg->unlock();
    $say('not migrated, the old settings are kept in OPNsense/ShaperAssistant: ' . implode('; ', $errors));
    exit(0);
}
$mdl->serializeToConfig();
$root = $cfg->object();
unset($root->OPNsense->ShaperAssistant[0]);
$cfg->save();
$say(sprintf('settings of os-shaper-assistant moved to Shaper Insight (%d priorities, %d interface choices, '
    . '%d link speeds); the old section is removed', $copied['priorities'], $copied['choices'], $copied['links']));
