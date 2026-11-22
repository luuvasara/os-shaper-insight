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
 * The only code in this plugin that changes the configuration. Called by apply.py (as root,
 * through configd), never by the web server directly.
 *
 *   writer.php restore-point            copy of the current config.xml in /conf/backup, prints its path
 *   writer.php write <plan> <kind> <refs> add one kind of the plan's objects (origin ShaperInsight);
 *                                        with kind rule, also disable the listed user rules
 *   writer.php snapshot <file>          save the sections an apply touches (TrafficShaper, Cake)
 *   writer.php restore-sections <file>  put only those sections back: the revert. Anything else
 *                                        changed meanwhile (another admin, DHCP, aliases) stays
 *   writer.php restore <backup file>    put a whole backup back (manual safety net, not automatic)
 *   writer.php remove <ledger.json>     delete the objects the ledger lists (uuids recorded at apply
 *                                        time), switch the listed user rules back on
 *
 * Output: one JSON object. Exit 0 on success, 1 on failure.
 */

require_once('script/load_phalcon.php');

use OPNsense\Core\Config;
use OPNsense\TrafficShaper\TrafficShaper;

const ORIGIN = 'ShaperInsight';
/* objects made under the plugin's earlier name (os-shaper-assistant) are ours too */
const OURS = ['ShaperInsight', 'ShaperAssistant'];

function ours_cake($descr)
{
    return strpos($descr, '[Insight] ') === 0 || strpos($descr, '[Assistant] ') === 0;
}

function out($data, $ok = true)
{
    echo json_encode($data) . PHP_EOL;
    exit($ok ? 0 : 1);
}

function cake_model()
{
    return class_exists('OPNsense\Cake\Cake') ? new \OPNsense\Cake\Cake() : null;
}

function validation_errors($mdl)
{
    $errors = [];
    foreach ($mdl->performValidation() as $msg) {
        $errors[] = $msg->getField() . ': ' . $msg->getMessage();
    }
    return $errors;
}

function save_models($models, $description)
{
    foreach ($models as $mdl) {
        $mdl->serializeToConfig(false, true);
    }
    Config::getInstance()->save(['description' => $description]);
}

/* the configuration sections an apply changes; the revert puts back exactly these */
const SECTIONS = ['TrafficShaper', 'Cake'];

function section_xpath()
{
    $root = dom_import_simplexml(Config::getInstance()->object());
    return [$root->ownerDocument, new DOMXPath($root->ownerDocument)];
}

$cmd = $argv[1] ?? '';

if ($cmd == 'snapshot') {
    [$doc, $xp] = section_xpath();
    $snap = [];
    foreach (SECTIONS as $name) {
        $node = $xp->query('/opnsense/OPNsense/' . $name)->item(0);
        $snap[$name] = $node !== null ? $doc->saveXML($node) : null;
    }
    $file = $argv[2] ?? '';
    /* atomic and private from the first byte: a temporary file in the same directory, created
       0600, then rename(); a power loss leaves the old restore point, never a truncated one (S13) */
    $tmp = $file . '.tmp' . getmypid();
    $old = umask(0077);
    $fh = $file !== '' ? @fopen($tmp, 'x') : false;
    umask($old);
    $data = json_encode($snap);
    if (
        $fh === false || fwrite($fh, $data) !== strlen($data) || !fflush($fh) || !fclose($fh) ||
        !rename($tmp, $file)
    ) {
        @unlink($tmp);
        out(['status' => 'failed', 'message' => 'cannot write the snapshot'], false);
    }
    out(['status' => 'ok', 'file' => $file, 'sections' => array_keys(array_filter($snap))]);
} elseif ($cmd == 'restore-sections') {
    $snap = json_decode(@file_get_contents($argv[2] ?? ''), true);
    if (!is_array($snap)) {
        out(['status' => 'failed', 'message' => 'no snapshot'], false);
    }
    Config::getInstance()->lock();
    [$doc, $xp] = section_xpath();
    $parent = $xp->query('/opnsense/OPNsense')->item(0);
    foreach (SECTIONS as $name) {
        if (!array_key_exists($name, $snap)) {
            continue;
        }
        $cur = $xp->query('/opnsense/OPNsense/' . $name)->item(0);
        if ($snap[$name] === null) {
            if ($cur !== null) {
                $cur->parentNode->removeChild($cur);
            }
            continue;
        }
        $frag = new DOMDocument();
        if (!$frag->loadXML($snap[$name])) {
            Config::getInstance()->unlock();
            out(['status' => 'failed', 'message' => "snapshot of $name is not valid XML"], false);
        }
        $new = $doc->importNode($frag->documentElement, true);
        if ($cur !== null) {
            $cur->parentNode->replaceChild($new, $cur);
        } else {
            $parent->appendChild($new);
        }
    }
    Config::getInstance()->save(['description' => 'Shaper Insight: put the shaper sections back']);
    Config::getInstance()->unlock();
    out(['status' => 'ok']);
} elseif ($cmd == 'restore-point') {
    out(['status' => 'ok', 'file' => Config::getInstance()->backup()]);
} elseif ($cmd == 'restore') {
    $file = realpath($argv[2] ?? '');
    $dir = realpath('/conf/backup');
    if (
        $file === false || $dir === false || dirname($file) !== $dir ||
        !preg_match('/^config-[0-9._]+\.xml$/', basename($file))
    ) {
        out(['status' => 'failed', 'message' => 'not a configuration backup: ' . ($argv[2] ?? '')], false);
    }
    // as core's BackupController::revertBackupAction: no lock() around it. restoreBackup()
    // replaces the open file handle, so a lock taken before would block save() forever.
    $cfg = Config::getInstance();
    $ok = $cfg->restoreBackup($file);
    if ($ok) {
        $cfg->save(['description' => 'Shaper Insight: put restore point back']);
    }
    out(['status' => $ok ? 'ok' : 'failed', 'file' => $file], $ok);
} elseif ($cmd == 'write') {
    /*
     * writer.php write <plan.json> <pipe|queue|rule|cake> <refs.json>
     * One kind per process, in dependency order (apply.py runs pipe, queue, rule, cake): a
     * ModelRelationField (queue -> pipe, rule -> queue, CAKE entry -> pipe) checks the saved
     * configuration and caches what it found for the rest of the process, so the kind it
     * points at must be saved by an earlier process. refs.json carries plan ref -> uuid.
     */
    $plan = json_decode(@file_get_contents($argv[2] ?? ''), true);
    $kind = $argv[3] ?? '';
    $refsfile = $argv[4] ?? '';
    if (!is_array($plan) || !isset($plan['objects']) || !in_array($kind, ['pipe', 'queue', 'rule', 'cake'])) {
        out(['status' => 'failed', 'message' => 'usage: write <plan.json> <pipe|queue|rule|cake> <refs.json>'], false);
    }
    $refs = json_decode(@file_get_contents($refsfile), true) ?: [];
    /* lock FIRST: lock() reloads config.xml, and a model built before it would save an older
       copy of its section over a save made in between (review S9; remove does the same) */
    Config::getInstance()->lock();
    if ($kind == 'cake') {
        $mdl = cake_model();
        if ($mdl === null) {
            out(['status' => 'ok', 'created' => [], 'disabled' => [], 'note' => 'os-cake not installed']);
        }
        $list = $mdl->pipes->pipe;
    } else {
        $mdl = new TrafficShaper();
        $list = $kind == 'pipe' ? $mdl->pipes->pipe : ($kind == 'queue' ? $mdl->queues->queue : $mdl->rules->rule);
    }
    $taken = [];
    if ($kind == 'pipe' || $kind == 'queue') {
        foreach ($list->iterateItems() as $item) {
            $taken[] = (string)$item->number;
        }
    }
    $created = [];
    $changed = [];
    foreach ($plan['objects'] as $o) {
        if ($o['kind'] != $kind) {
            continue;
        }
        $fields = $o['model'];
        foreach (['pipe_ref' => 'pipe', 'target_ref' => 'target'] as $ref => $field) {
            if (!empty($o[$ref])) {
                $fields[$field] = $refs[$o[$ref]] ?? '';
            }
        }
        if (!empty($o['uuid'])) {
            /* Shaper Insight's own object from an earlier apply: change it in place. Core objects
               only while they still carry our origin, CAKE entries only under our name prefix */
            $prefix = $kind == 'cake' ? 'pipes.pipe.' : ['pipe' => 'pipes.pipe.', 'queue' => 'queues.queue.',
                'rule' => 'rules.rule.'][$kind];
            $node = $mdl->getNodeByReference($prefix . $o['uuid']);
            $mine = $node !== null && ($kind == 'cake' ? ours_cake((string)$node->description)
                : in_array((string)$node->origin, OURS, true));
            if (!$mine) {
                Config::getInstance()->unlock();
                out(['status' => 'failed',
                    'message' => "$kind {$o['uuid']} is no longer Shaper Insight's; preview the plan again"], false);
            }
            unset($fields['number']);
            $node->setNodes($fields);
            $changed[] = $o['uuid'];
            if (!empty($o['ref'])) {
                $refs[$o['ref']] = $o['uuid'];
            }
            continue;
        }
        if (isset($fields['number']) && in_array($fields['number'], $taken)) {
            Config::getInstance()->unlock();
            out(['status' => 'failed',
                'message' => "$kind number {$fields['number']} is taken; preview the plan again"], false);
        }
        $node = $list->Add();
        $node->setNodes($fields);
        $uuid = $node->getAttributes()['uuid'];
        $created[] = $uuid;
        if (!empty($o['ref'])) {
            $refs[$o['ref']] = $uuid;
        }
    }
    $disabled = [];
    if ($kind == 'rule') {
        foreach ($plan['disable_rules'] ?? [] as $uuid) {
            $node = $mdl->getNodeByReference('rules.rule.' . $uuid);
            if ($node !== null && !in_array((string)$node->origin, OURS, true)) {
                $node->enabled = '0';
                $disabled[] = $uuid;
            }
        }
    }
    $errors = validation_errors($mdl);
    if (!empty($errors)) {
        Config::getInstance()->unlock();
        out(['status' => 'failed', 'message' => "validation failed ($kind)", 'errors' => $errors], false);
    }
    save_models([$mdl], "Shaper Insight: apply ({$kind}s)");
    Config::getInstance()->unlock();
    file_put_contents($refsfile, json_encode($refs));
    out(['status' => 'ok', 'created' => $created, 'changed' => $changed, 'disabled' => $disabled]);
} elseif ($cmd == 'remove') {
    /* only what the ledger lists (uuids recorded when an apply was confirmed); core objects only
       while they still carry our origin, CAKE entries only under our name prefix: a hand-made
       object is never removed. An object that something outside the ledger still points at (a
       queue or rule you made on a Shaper Insight pipe) is KEPT, with the chain it needs, and listed
       under "kept", so nothing is left with a dangling reference (review M3). */
    $ledger = json_decode(@file_get_contents($argv[2] ?? ''), true);
    if (!is_array($ledger)) {
        out(['status' => 'failed', 'message' => 'no ledger'], false);
    }
    Config::getInstance()->lock();
    $ts = new TrafficShaper();
    $cake = cake_model();
    $paths = ['rule' => 'rules.rule', 'queue' => 'queues.queue', 'pipe' => 'pipes.pipe'];
    $go = [];                   // uuid => kind, ours and listed
    foreach ($paths as $kind => $path) {
        foreach ($ledger[$kind] ?? [] as $uuid) {
            $node = $ts->getNodeByReference($path . '.' . $uuid);
            if ($node !== null && in_array((string)$node->origin, OURS, true)) {
                $go[$uuid] = $kind;
            }
        }
    }
    $cakego = [];
    if ($cake !== null) {
        foreach ($ledger['cake'] ?? [] as $uuid) {
            $node = $cake->getNodeByReference('pipes.pipe.' . $uuid);
            if ($node !== null && ours_cake((string)$node->description)) {
                $cakego[$uuid] = (string)$node->pipe;
            }
        }
    }
    /* who points at what: queue->pipe, rule->target (pipe or queue), CAKE entry->pipe */
    $edges = [];
    foreach ($ts->queues->queue->iterateItems() as $uuid => $q) {
        $edges[] = [(string)$uuid, (string)$q->pipe, 'queue "' . (string)$q->description . '"'];
    }
    foreach ($ts->rules->rule->iterateItems() as $uuid => $r) {
        $edges[] = [(string)$uuid, (string)$r->target, 'rule "' . (string)$r->description . '"'];
    }
    if ($cake !== null) {
        foreach ($cake->pipes->pipe->iterateItems() as $uuid => $c) {
            $edges[] = [(string)$uuid, (string)$c->pipe, 'CAKE entry "' . (string)$c->description . '"'];
        }
    }
    $kept = [];
    do {                        // keep anything a kept or outside object needs, until stable
        $more = false;
        foreach ($edges as [$from, $to, $label]) {
            $from_goes = isset($go[$from]) || isset($cakego[$from]);
            if (!$from_goes && isset($go[$to])) {
                $kept[] = $go[$to] . ' ' . $to . ' (used by ' . $label . ')';
                unset($go[$to]);
                $more = true;
            }
        }
    } while ($more);
    $keep_uuids = array_map(fn($k) => explode(' ', $k)[1], $kept);
    foreach ($cakego as $uuid => $pipe) {
        if (in_array($pipe, $keep_uuids)) {     // a kept pipe keeps its CAKE settings too
            unset($cakego[$uuid]);
            $kept[] = 'cake ' . $uuid . ' (its pipe is kept)';
            $keep_uuids[] = $uuid;
        }
    }
    $removed = ['cake' => 0, 'rule' => 0, 'queue' => 0, 'pipe' => 0, 'enabled_again' => 0];
    foreach (array_keys($cakego) as $uuid) {
        if ($cake->pipes->pipe->del($uuid)) {
            $removed['cake']++;
        }
    }
    foreach ($paths as $kind => $path) {    // reverse dependency order: rules, queues, pipes
        [$group, $item] = explode('.', $path);
        foreach ($go as $uuid => $k) {
            if ($k == $kind && $ts->$group->$item->del($uuid)) {
                $removed[$kind]++;
            }
        }
    }
    foreach ($ledger['enable'] ?? [] as $uuid) {
        $node = $ts->getNodeByReference('rules.rule.' . $uuid);
        // only rules still switched off: one you switched on or edited since stays as you left it
        if ($node !== null && !in_array((string)$node->origin, OURS, true) && (string)$node->enabled === '0') {
            $node->enabled = '1';
            $removed['enabled_again']++;
        }
    }
    $errors = validation_errors($ts);
    if ($cake !== null) {
        $errors = array_merge($errors, validation_errors($cake));
    }
    if (!empty($errors)) {
        Config::getInstance()->unlock();
        out(['status' => 'failed', 'message' => 'validation failed; nothing was removed', 'errors' => $errors], false);
    }
    save_models($cake !== null ? [$ts, $cake] : [$ts], 'Shaper Insight: remove what it created');
    Config::getInstance()->unlock();
    out(['status' => 'ok', 'removed' => $removed, 'kept' => $kept, 'kept_uuids' => $keep_uuids]);
} else {
    out(['status' => 'failed',
        'message' => 'usage: writer.php snapshot|restore-sections|restore-point|write|restore|remove'], false);
}
