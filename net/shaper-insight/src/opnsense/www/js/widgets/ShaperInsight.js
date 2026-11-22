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

// Dashboard widget: is the shaper doing what its configuration says? One line per pipe.
export default class ShaperInsight extends BaseTableWidget {
    constructor() {
        super();
        this.tickTimeout = 15; // seconds; the status script caches its kernel reads for 3 s
    }

    getMarkup() {
        const $table = this.createTable('shaperinsight-table', {headerPosition: 'left'});
        return $('<div id="shaperinsight-container"></div>').append($table);
    }

    // the apply countdown banner, shared with the plugin pages (common.js); loaded only when needed
    showCountdown(apply) {
        if (!apply && !window.SA) {
            return;
        }
        if (window.SA) {
            window.SA.countdown(apply);
            return;
        }
        $('head').append('<link rel="stylesheet" href="/ui/css/shaperinsight/shaperinsight.css">');
        $.getScript('/ui/js/shaperinsight/common.js').done(() => window.SA.countdown(apply));
    }

    esc(s) {
        return $('<div/>').text(s === undefined || s === null ? '' : String(s)).html();
    }

    badge(level, text) {
        const cls = {ok: 'success', info: 'info', warn: 'warning', bad: 'danger'}[level] || 'info';
        const icon = {ok: 'fa-check-circle', info: 'fa-info-circle', warn: 'fa-exclamation-triangle', bad: 'fa-times-circle'}[level] || 'fa-info-circle';
        // the word carries the meaning in the body colour; only the icon takes the theme colour
        // (the stock label-* backgrounds fail contrast with white text)
        return `<span style="font-weight:600;white-space:nowrap"><i class="fa ${icon} text-${cls}" aria-hidden="true"></i> ${this.esc(text)}</span>`;
    }

    rate(bps) {
        if (bps === null || bps === undefined) {
            return '';
        }
        return bps >= 1e9 ? `${(bps / 1e9).toPrecision(3)} Gbit/s` : `${Math.round(bps / 1e6)} Mbit/s`;
    }

    async onWidgetTick() {
        if (document.visibilityState === 'hidden') {
            return;
        }
        let data = await this.ajaxCall('/api/shaperinsight/status/summary');
        if (data && data._probe === '&lt;') {
            // the API HTML-escapes browser responses; undo it once so esc() is the only escaping
            data = JSON.parse(JSON.stringify(data).replace(/&(lt|gt|amp);/g, (m, e) => ({lt: '<', gt: '>', amp: '&'}[e])));
        }
        this.showCountdown(data ? data.apply : null);
        if (!data || !data.summary) {
            $('#shaperinsight-table').html(`<a href="/ui/shaperinsight/">${this.translations.unavailable}</a>`);
            return;
        }
        if (!this.dataChanged('shaperinsight', data.pipes.concat([data.summary]))) {
            return;
        }
        const words = {ok: this.translations.ok, info: this.translations.note, warn: this.translations.check, bad: this.translations.problem};
        const summary = data.summary.text.replace(/\{\{term:([a-z0-9-]+)\}\}/g, (m, id) => id.toUpperCase());
        const rows = [[this.badge(data.summary.level, words[data.summary.level]), this.esc(summary)]];
        data.pipes.filter((p) => p.enabled).forEach((p) => {
            const name = `${this.translations.pipe} ${p.number}${p.description ? ' ' + p.description : ''}`;
            rows.push([`<a href="/ui/shaperinsight/" style="color:inherit;text-decoration:underline">${this.esc(name)}</a>`,
                `${this.badge(p.level, p.loaded || this.translations.notloaded)} ${this.esc(this.rate(p.bandwidth_kernel))}`]);
        });
        if (data.pipes.length === 0) {
            rows.push([this.translations.pipes, `<a href="/ui/trafficshaper">${this.translations.nopipes}</a>`]);
        }
        this.updateTable('shaperinsight-table', rows);
    }
}
