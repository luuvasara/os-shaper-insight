{#
 # Copyright (C) 2026 os-shaper-insight contributors
 # All rights reserved.
 #
 # Redistribution and use in source and binary forms, with or without
 # modification, are permitted provided that the following conditions are met:
 #
 # 1. Redistributions of source code must retain the above copyright notice,
 #    this list of conditions and the following disclaimer.
 #
 # 2. Redistributions in binary form must reproduce the above copyright
 #    notice, this list of conditions and the following disclaimer in the
 #    documentation and/or other materials provided with the distribution.
 #
 # THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 # INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 # AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 # AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 # OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 # POSSIBILITY OF SUCH DAMAGE.
 #}
<link rel="stylesheet" href="{{ cache_safe('/ui/css/shaperinsight/shaperinsight.css') }}">
<script src="{{ cache_safe('/ui/js/shaperinsight/ipcompare.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/verdicts.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/delta.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/common.js') }}"></script>
<script src="{{ cache_safe('/ui/js/chart.umd.min.js') }}"></script>
<script>
/* what each graph loads when it is on screen, nothing before (G1: Chart.js only, already here; G2: plain
   SVG; G3/G4: a vendored Chart.js plugin; G5: core's matrix plugin) */
window.SA_LAZY = {
    g1: ['{{ cache_safe("/ui/js/shaperinsight/sharing/g1.js") }}'],
    g2: ['{{ cache_safe("/ui/js/shaperinsight/sharing/g2.js") }}'],
    g3: ['{{ cache_safe("/ui/js/shaperinsight/vendor/chartjs-chart-treemap.min.js") }}', '{{ cache_safe("/ui/js/shaperinsight/sharing/g3.js") }}'],
    g4: ['{{ cache_safe("/ui/js/shaperinsight/vendor/chartjs-chart-sankey.min.js") }}', '{{ cache_safe("/ui/js/shaperinsight/sharing/g4.js") }}'],
    g5: ['{{ cache_safe("/ui/js/chartjs-plugin-matrix.min.js") }}', '{{ cache_safe("/ui/js/shaperinsight/sharing/g5.js") }}']
};
</script>
<script src="{{ cache_safe('/ui/js/shaperinsight/sharing/model.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/sharing/views.js') }}"></script>
<script src="{{ cache_safe('/ui/js/shaperinsight/sharing/settings.js') }}"></script>

<div class="content-box sa-page">
    <section class="sa-section" aria-labelledby="sa-h-sharing">
        <h2 id="sa-h-sharing">{{ lang._('Who uses each pipe') }}</h2>
        <p class="sa-lead">{{ lang._('Each pipe as the shaper sees it: who uses it, how close it runs to its cap, where the drops land and when it was limiting. A sampler on the firewall records them every 10 s, so the history is there as soon as the page opens.') }}</p>
        <p class="sa-meta" id="sa-sampler" aria-live="polite">–</p>
        <p class="sa-meta">
            <label for="sa-window">{{ lang._('Show the last') }}</label>
            <select id="sa-window" class="form-control sa-inline-select">
                <option value="600">{{ lang._('10 minutes') }}</option>
                <option value="3600" selected>{{ lang._('hour') }}</option>
                <option value="21600">{{ lang._('6 hours') }}</option>
                <option value="259200">{{ lang._('3 days') }}</option>
                <option value="2592000">{{ lang._('30 days') }}</option>
                <option value="31536000">{{ lang._('year') }}</option>
            </select>
            <label for="sa-shown">{{ lang._('Hosts drawn') }}</label>
            <select id="sa-shown" class="form-control sa-inline-select"
                    title="{{ lang._('The busiest hosts over the shown window get a colour; the rest are one grey band. At most 8: more colours cannot be told apart.') }}">
                <option>3</option><option>4</option><option>5</option><option>6</option><option>7</option>
                <option selected>8</option>
            </select>
            <button type="button" id="sa-toggle" class="btn btn-default btn-xs">–</button>
            <span id="sa-hosts-note"></span>
        </p>
        <details class="sa-rec" id="sa-rec">
            <summary>{{ lang._('Recording settings') }}: <b id="sa-rec-level"></b></summary>
            <div id="sa-rec-body"><p class="sa-meta">{{ lang._('Reading the settings...') }}</p></div>
        </details>
        <details class="sa-explain">
            <summary>{{ lang._('Why a speed test cannot show how the line is shared') }}</summary>
            <p>{{ lang._('A speed test sees only its own device. When the line is shared fairly, it gets whatever the other hosts leave unused at that moment, so "800 while the backups ran" happens with fair sharing, with unfair sharing and with no shaping at all. Sharing only shows when two hosts that both keep sending are measured at the same time, which is what the graphs below do.') }}</p>
            <p>{{ lang._('The number itself leans high: Ookla sorts its samples, drops the two fastest and the slowest quarter, and averages the rest, measured on the client.') }}
                <a href="https://www.speedtest.net/help/guides/how-does-speedtest-measure-my-network-speeds" rel="noopener noreferrer" target="_blank">{{ lang._('Ookla: how Speedtest measures') }}</a>.
                {{ lang._('Its ceiling is below the configured rate: TCP payload is about rate × 1448 / (1500 + overhead).') }}</p>
        </details>
        <details class="sa-explain">
            <summary>{{ lang._('How hosts are counted') }}</summary>
            <p>{{ lang._('Hosts come from the firewall’s connection states (pf), sampled every 10 s (less often on a firewall with many states; paused above 50,000). A connection through the firewall has two states, one per side, and pf counts a packet on the state of the side where it comes in, even when CAKE then drops it. So each number in the hosts table says how it was obtained (“How counted”):') }}</p>
            <ul>
                <li>{{ lang._('exact: counted where the packet leaves the firewall, after CAKE. Upload is read on the internet side of the connection, download on its LAN side. Ex.: a laptop whose download arrived at 28.9 Mbit/s and was cut to 24.9 by CAKE shows 24.9. In the lab these matched what the hosts received within about 1 %.') }}</li>
                <li>{{ lang._('counted, checked: a download with no LAN-side state (the firewall’s own downloads, or a connection whose two states could not be matched) is only counted before CAKE. When the pipe’s own total shows nothing to correct (within 2 %), that count is kept. Ex.: the firewall’s own TCP download read 11.8 Mbit/s where about 11.6 arrived.') }}</li>
                <li>{{ lang._('fair-share estimate: when that traffic does need correcting (CAKE dropped part of it), it gets its fair share of what the pipe sent beyond the exact hosts, inside its CAKE class, the way CAKE shares the line. Ex.: the firewall offered 10 Mbit/s while only 3 were left: it shows 3.') }}</li>
                <li>{{ lang._('scaled: as above, but when it is not known which CAKE class those hosts use (a host marks its own traffic, or a priority set by port), their counts are scaled down by one factor to what the pipe sent. Less accurate: a light host is cut as much as a heavy one.') }}</li>
            </ul>
            <p>{{ lang._('What pf counted on the internet side before CAKE is kept as “offered” (in the history API); on download the difference between offered and sent is what CAKE dropped. A connection that ends between two samples loses its last seconds. “Not attributed” is what the pipe carried that no state accounts for.') }}</p>
        </details>
    </section>
    <section class="sa-section sa-viewbar-box" aria-labelledby="sa-h-view">
        <h2 id="sa-h-view" class="sr-only">{{ lang._('View') }}</h2>
        <div id="sa-viewbar"></div>
        <div id="sa-pipebar" class="sa-pipebar"></div>
    </section>
    <section class="sa-section" aria-label="{{ lang._('Pipes') }}">
        <div id="sa-view"><p>{{ lang._('Reading the history...') }}</p></div>
        <div id="sa-na"></div>
    </section>
    <section class="sa-section">
        <p class="sa-meta">{{ lang._('The pipe history is in RRD files in /var/db/rrd (10 s for 6 hours, 1 minute for 3 days, 10 minutes for 30 days, 1 hour for a year). They survive reboots, and Reporting: Health lists them under “shaper”. Hosts are not in the RRDs: an RRD has a fixed set of series, and the busiest hosts change all the time; they are kept for 6 hours in /var/run, which a reboot clears.') }}</p>
    </section>
</div>
