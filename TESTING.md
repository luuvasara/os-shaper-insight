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
- Not done: a pristine VM per run (install and uninstall tests ran on the same dev VM), and
  any test on real hardware other than one cost measurement noted below.

## Automated checks

| Check | Result (at 1.0.f) |
|---|---|
| `python3 -m unittest discover -s tests` | 149 tests, OK |
| `tools/claims/build.py --check` | 26 claim ids used, 27 in `claims.json`, 0 problems |
| ruff `--select F`, pycodestyle (E501 ignored), `php -l` (PHP 8.5), phpcs (core ruleset), shellcheck | clean |
| axe-core 4 (WCAG 2.0/2.1/2.2 A+AA + best practice), every page, 1920x1080 and 390x844, light and dark | 0 violations in this plugin's markup; the remaining hits come from OPNsense core pages (menu search box, sidebar toggle size, navbar landmarks, dark footer links) |
| `tools/shots/terms-check.js` | every technical term on 6 rendered pages has a glossary chip; fails as intended when chips are removed (`SA_TERMS_NEGATIVE=1`) |
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
| History sampler | 150 ms CPU per 10 s sample with 6231 pf states (about 1.5 % of one core), 16 ms with 60 states; RSS 17 MB; fixed-size RRD files (about 1.9 MB per pipe plus 1.3 MB per diffserv pipe) |

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
| Security review | S1-S14, I1 | S1 uninstall hook failing open; S2 huge aliases; S3 stale control files; S5 a GET that changed state; S6 neighbour list for the read-only role; S9 model built before `Config::lock()`; S10 pid read from a stale file; S12 redactor bypasses |

## Known gaps

- Interactive latency gains from priorities are not visible in the lab (sub-millisecond round
  trips); they need a real line.
- The bufferbloat defaults use Cloudflare's speed-test endpoints; their terms for automated use
  were not reviewed.
- Upstream conventions not met (reasons an upstream merge is unlikely as is): the 60 s confirm
  timer with restore points, overriding core's `firewall_script` for the priority rules,
  writing core and os-cake model sections, and the size of the plugin.
