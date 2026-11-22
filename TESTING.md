# Testing

Written: 2026-11-22. A summary of how os-shaper-insight was tested and what was found. The
lab scripts and the full lab notes are not published (they describe the test host); this file
keeps the methods, the results and the finding ids that comments in the code and tests refer
to (H/M/L, N, F, S, I).

## Environment

- One throw-away OPNsense 26.7.9_3 VM (amd64, 2 vCPUs pinned to one physical core), with
  os-cake 1.4.15 / cake-kmod 1.5.15 from that project's verified release files.
- The VM's LAN and WAN sides are network namespaces and bridges on the host, with iperf3
  endpoints as LAN hosts and as the "internet"; no physical NIC is involved. A second WAN uses
  QEMU user-mode networking for real internet access.
- Topology variants for detection were built on the same VM: DHCP WAN, PPPoE, several VLAN
  WANs on one port, two physical WAN ports, IPv6 only, a static transit network to a
  downstream router, WireGuard tunnels with gateways, no os-cake, and a hand-made shaper.
- Not done: a pristine VM per run (install and uninstall tests ran on the same dev VM).

## Production

Running on the author's gateway since 2026-11-22: bare metal, OPNsense 26.7.9, 6-core 10th-gen
Intel, Intel `em` WAN and `igb` LAN NICs, symmetric gigabit fibre with five WAN VLANs on one port
sharing one pipe pair, os-cake 1.4.15 / cake-kmod 1.5.15.

- In use: the status, sharing and compiled-config pages, the RRD history, and one Background
  priority on a host alias (a group of VPN containers) with CAKE `diffserv4`. The shaper itself
  was configured by hand; the setup page's Apply and Remove have not been used in production.
- Installs: 1.0.f, then an upgrade to 1.0.h. Before and after each, a snapshot of the shaper
  configuration, the loaded dummynet objects, the priority rules, tables and crontab: shaper and
  marks unchanged, rule counters kept growing (no reload), the only addition was the 1.0.h
  sampler-watchdog cron line. A boot-environment snapshot was taken first each time.
- Measured: history sampler about 160 ms of CPU a sample at about 4,700 pf states, 24 MB RSS
  (1.0.f). With the Background priority on and nothing else busy, the Bulk tin carried 544 Mbit/s
  of upload: it takes the free line, as designed.
- Short so far; a longer report will follow on the os-cake discussion.

## Automated checks

| Check | Result (at 1.0.h unless noted) |
|---|---|
| `python3 -m unittest discover -s tests` | 201 tests, OK |
| `tools/claims/build.py --check` | 26 claim ids used, 27 in `claims.json`, 0 problems |
| ruff `--select F`, pycodestyle (E501 ignored), `php -l` (PHP 8.5), phpcs (core ruleset), shellcheck | clean (at 1.0.f) |
| axe-core 4 (WCAG 2.0/2.1/2.2 A+AA + best practice), every page, 1920x1080 and 390x844, light and dark | 0 violations in this plugin's markup; the remaining hits come from OPNsense core pages (menu search box, sidebar toggle size, navbar landmarks, dark footer links) |
| `tools/shots/terms-check.js` | every technical term on 6 rendered pages has a glossary chip; fails as intended when chips are removed (`SA_TERMS_NEGATIVE=1`) |
| `tools/shots/tables-check.js` | every table on the five pages is built by the shared table component: sortable by keyboard, `aria-sort`, an empty state, a swatch where rows have a chart series, linked object names with hover, no sideways scroll at 390 px; fails as intended on a plain table (`SA_TABLES_NEGATIVE=1`) |
| `tools/shots/hosts-stress.js` | the sharing page answered with 1,000 fake hosts (documentation addresses): default view 4 rows, the opened list about 41 rows and 500 nodes in the DOM, search finds one host and says so, IP sort puts IPv4 first |
| `tools/shots/crosshair.js` | the three stacked bufferbloat charts share one x mapping and a hover activates the same second on all three |

## Lab tests of Apply, Remove and uninstall

Scripted cycles ran apply + confirm, Remove, apply + timeout and Remove again, and after each
step compared configuration and kernel (zero stale and zero missing objects required):

| Test | Result |
|---|---|
| Writer fails validation | reverted at once; config.xml identical to before |
| No confirmation within 60 s; browser closed during the countdown | reverted; kernel back to the original pipes; a new browser shows why |
| Unrelated change (an alias) during the countdown, then timeout | the alias stays; only the shaper and CAKE sections are restored |
| `kill -9` of the job, reboot during the countdown | reverted by the next status call or by the boot hook |
| Second apply, Remove or wrong-token confirm while a job runs | refused; one job process alive |
| Re-apply at another rate | objects changed in place, no duplicates |
| `make upgrade` with a confirmed setup | sections, priority marks and kernel pipes identical afterwards |
| `pkg delete` | confirmed objects kept and still loaded; marks, tables, cron and runtime files gone; the same after a reboot; a reinstall picks the setup up and Remove still works |
| Install with no priorities | no ipfw reload, no tables, no ipfw set touched; rule counters not reset |

## Priorities (DSCP marks)

Priorities were first tried as pf rules with their own pipes. In the lab that broke NAT for
queued packets above the pipe rate (packets left with their private source address) and shaped
priority traffic twice (pf and ipfw are separate hooks). The plugin therefore marks DSCP with
ipfw `setdscp` and lets CAKE sort marked traffic into tins of the same pipe.

Results (WAN pipes 40/20 Mbit/s, CAKE diffserv4, dual-host isolation): marked traffic moved to
the chosen tin in both directions; CAKE picks the tin before it washes the mark (so wash on
both directions keeps marks off the line); under contention AF41 vs unmarked split evenly, CS1
(background) vs AF41 gave 1.0 vs 17.5 Mbit/s up and 2.1 vs 34.9 down, with the line fully
used; EF hurt a bulk host (Voice tin threshold), so the picker offers no EF class; marks from
the internet are cleared; the rules survive core Shaper/CAKE Apply, filter reload, ipfw
reload and a reboot. A port priority (`udp/51820`) matched as expected and the Bulk tin held
about 1/16 of the rate under saturation.

CAKE's fair sharing is per address: a device sending over IPv4 and IPv6 at once got 66.5 % of
the line against another device instead of 50 %.

## Bufferbloat test page

Run against the QEMU user-mode WAN (which answers ping itself, so these are not internet
numbers and the page labels them LAB): unshaped grade A, 6.3 ms added; with the plan at 80/20
Mbit/s (CAKE) A+, 0.8 ms added. Cancel stops every load process.

## Performance budget

| Budget | Result on the 2-vCPU lab VM |
|---|---|
| Status API call under 50 ms CPU | 40 ms warm, 49 ms on the normal 5 s poll (mean of 20; Python start-up alone is 20 ms) |
| Polling | widget every 15 s, page every 5 s, both paused while the tab is hidden |
| Forks per poll | one dnctl, one ipfw, one sysctl; snapshot cached 3 s and shared by page and widget |
| Page JavaScript | shared `common.js` 22 KB plus 4-26 KB inline per page (source size at 1.0.f, uncompressed); core's Chart.js, no new library |
| History sampler (1.0.f, 2 vCPUs) | 150 ms CPU per 10 s sample with 6231 pf states (about 1.5 % of one core), 16 ms with 60 states; RSS 17 MB; fixed-size RRD files (about 1.9 MB per pipe plus 1.3 MB per diffserv pipe) |
| History sampler (1.0.g, 4-core lab VM) | idle about 15 ms a sample and 18 MB; 22,500 states about 230 ms a sample with the per-host read every 30 s, RSS 46 MB; 59,000 states: per-host view paused, 5 ms a sample; RSS back to 18.7 MB after the states expired |

`pfctl -vss` took 20 ms with about 6000 states on a busy home firewall (the one measurement on
real hardware).

Speed-test analysis (sharing page): a rate computed from a timestamp taken before the counters
were read could show 900 Mbit/s on an 840 Mbit/s pipe. Rates now use the time of each read,
pairs less than 2 s apart are skipped, and points above the payload cap (rate x 1500 / (1500 +
overhead), about 817 Mbit/s for 840 with 42 bytes) are flagged instead of drawn silently.

## Review rounds and finding ids

Each round's fixes have regression tests named after the ids.

| Round | Ids | Main findings |
|---|---|---|
| Independent review | H1-H3, M1-M8, L1-L8 | H1 a dead job reverting over later edits; H2 uninstall during a countdown; H3 slow links shaped above line rate; M3/M4/M8 one lock and a plan digest so Apply writes what was previewed; M5 priority load failures; M6 fairness claims without CAKE |
| Verification 1-3 | N1-N8 | N1-N3 `make upgrade` is `pkg delete` + `pkg add`, so uninstall must keep a confirmed setup; N4 `pkg delete` before `register.php remove`; N5 install must be kernel-neutral; N6 "Disable mine" must say what it switches off; N8 file modes |
| Topology test | F1-F14 | F1 any gateway counted as internet (a 2.5 Gbit/s transit network was planned at 17/88 Mbit/s); F2 one rate pair for all links; F3 no override; F4 prefix-delegated LANs; F5 dynamic gateways only exist at runtime; F7 line-type overhead |
| Verification 4 | N9-N14 | N9 per-host download rates included what CAKE dropped (pf counts before the shaper: hosts summed to 107 % of the pipe); N10 a config save could switch the sampler off; N11 sampler cost grew with pf states without a cap; N12 a slow `pfctl` blanked the pipe history; N13 old readings judged by today's cap; N14 minor (restart state, broken or symlinked RRD files) |
| Security review | S1-S14, I1 | S1 uninstall hook failing open; S2 huge aliases; S3 stale control files; S5 a GET that changed state; S6 neighbour list for the read-only role; S9 model built before `Config::lock()`; S10 pid read from a stale file; S12 redactor bypasses |

## Verification 4 fixes (1.0.g)

| Id | Fix | Result |
|---|---|---|
| N9 | the host stack is scaled to what the pipe delivered; the offered rate is its own dashed line | lab under drops: 10,000 rows, none with hosts above the pipe (worst 38.3 vs 38.2 Mbit/s, rounding) |
| N10 | config.xml read under a shared lock with retries; a failed read is never "off"; cron watchdog `sampler.ensure` every 5 min and health check S10 | 32,000 and 34,000 config saves in 150 s: the sampler kept running, no empty rows |
| N11 | state read capped at 50,000 (per-host view paused, pipes go on), per-host interval adapts to its cost (10 to 60 s), memory given back after a flood | numbers in the performance table above |
| N12 | pipes read every tick on their own time stamp; `pfctl` cut at 5 s | `pfctl` delayed 30 s: no empty pipe rows, one log line |
| N13, N14 | each reading judged by the cap of its time; restarting state; a broken RRD moved aside after 3 failures; a symlinked RRD replaced | unit tests |
| `pkg add -f` | the sampler watches the package version file and stops on a change or removal | unit test; upgrade rehearsal 1.0.f to 1.0.g with priorities loaded: shaper, rules, sets, tables and counters unchanged, only the watchdog cron line added |

## Bulk tin (Background) status (1.0.g, 1.0.h)

With CAKE diffserv4 the Background class (Bulk tin) is guaranteed about 1/16 of the line and
takes the rest only when nothing else needs it. The sharing page says which of these applies,
from the newest 10 s step: **free line** (the other classes carry at most 2 % of the line),
**yielding** (Bulk near its floor, at most 1.5x, while the other classes fill a busy line),
**sharing**, **idle**, or **besteffort** (priorities have no effect). 1.0.g called 3 % of the line
in other traffic "free"; 1.0.h fixed that. Tests: `BulkTin` in `tests/test_sharing.py`.

## Sharing page with 100 and 1,000 hosts (host simulation)

A seeded generator fed the sharing page 100 and 1,000 hosts (10 % without a name, long and
non-Latin names, 15 % IPv6, random and shared MACs, Pareto-distributed rates) and the page was
measured in Chrome at 1920x1080 and 390x844, light and dark:

| | 100 hosts | 1,000 hosts |
|---|---|---|
| One series per host: chart update | 564 ms | 46.8 s (the page polls every 10 s) |
| Top-N chart: update | 18-36 ms | 18-32 ms |
| All table rows rendered / a 40-row window | 12-18 / 5 ms | 122-143 / 5 ms |
| DOM elements, list opened (all rows) | 1,562 | 14,434 |
| Every host's series for 6 h, gzip | 340 kB | 3.4 MB |
| Newest-step table only, gzip | 2.8 kB | 21 kB |

What changed because of it: at most 8 coloured hosts (more colours cannot be told apart; the
rest is one neutral band, "not attributed" a hatched one); a host keeps its colour while it is
shown (colour by rank repainted a visible host 8 times in 5 minutes); two palette colours that
read as grey or too light were dropped; the legend is HTML above the chart (Chart.js silently
dropped 57 of 102 legend entries); the full host list comes from a newest-step endpoint with
server-side search, sort and paging, fetched only while the list is open, and is windowed
beyond 100 rows; on a phone the IP, MAC and maker columns fold into a second line.

## Tables (1.0.g)

Every table uses one component on core's Tabulator: sortable by click or keyboard with
`aria-sort`, addresses sorted by value (IPv4 numerically, then IPv6; `tests/test_tables.py`
includes a control that a text sort differs), an empty state, Name / IP / MAC / Manufacturer
columns for hosts (maker from core's IEEE OUI list, nothing looked up outside the firewall),
"shared xN" for a MAC used by several addresses and "private (random) MAC" for locally
administered ones, and object names linked to their page in core.

## Known gaps

- Interactive latency gains from priorities are not visible in the lab (sub-millisecond round
  trips); they need a real line.
- The bufferbloat defaults use Cloudflare's speed-test endpoints; their terms for automated use
  were not reviewed.
- Upstream conventions not met (reasons an upstream merge is unlikely as is): the 60 s confirm
  timer with restore points, overriding core's `firewall_script` for the priority rules,
  writing core and os-cake model sections, and the size of the plugin.
