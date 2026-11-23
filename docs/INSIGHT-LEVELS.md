# Insight level: Full and Light

Written: 2026-11-23 (plugin 1.0.j). Updated: 2026-11-23 (1.0.k: pf read cost and the pf states limit). Measured on a lab VM (OPNsense 26.7, 2 vCPU, 2.5 GB RAM).

The sharing page has a "Recording settings" card (Firewall: Shaper: Shaper Insight: sharing). It sets an
**insight level**:

- **Full** (the default) records every host and refreshes the page every 10 s. It is meant for learning
  how the line is shared.
- **Light** turns the heavy parts down. It is meant for when you are content.

Choosing a level sets the settings below. Each setting can still be changed on its own; the card then says
**Custom**. Nothing stores the level itself: the page compares the three settings with the two presets.
That way the level can never disagree with the settings.

## What each setting costs

| Setting | Full | Light | What it costs, measured |
|---|---|---|---|
| Store every host's history (`general/store_all_hosts`) | on | off (busiest 16 per sample + the rest) | **RAM:** the 6 h host list in `/var/run`. With 2 pipes: 1.7–1.8 MB at top 16, whatever the host count; with every host, 9.3 MB at 100 hosts per pipe, 94 MB at 1,000 and 188 MB at 2,000. **CPU per page poll** (1 h window): 74–86 ms at top 16; with every host, 235 ms at 100 hosts per pipe, 1.8 s at 1,000 and 3.7 s at 2,000. **Answer size** when G2 or G5 asks for every host's series: 572 kB at 100 hosts, 4.5 MB at 1,000. The sampler's write side is small (0.17 / 1.55 / 2.83 ms per sample). Source: a synthetic ring on the lab VM, 2 pipes. |
| Read the hosts every (`general/hosts_interval`) | 10 s | 30 s | Each read lists the pf state table: about 16 ms + 0.027 ms per state since 1.0.k (1.0.j: 0.022 ms; 2026-11-22: 16 ms at 60 states, 150 ms at 6,231; 1.0.k's pairing adds 0.005 ms per state, 2026-11-23). 30 s cuts it to a third. The sampler already slows down by itself above 150 ms per read. |
| Refresh this page every (`general/poll_seconds`) | 10 s | 30 s | Each refresh makes the firewall read the history once, for every open page (the per-poll CPU above). It was in the model since 1.0 and unused until 1.0.j. The page never refreshes faster than every 10 s, so the old default 5 counts as 10. |

These are not part of the level, because they cost little:

- the pipe RRDs: fixed size, 1.9 MB per pipe plus 1.3 MB per diffserv pipe;
- "Hosts drawn" (3–8): only colours;
- the sampler itself: switching it off loses the history.

### Full vs Light on the lab (real traffic, 2 hosts)

A lab script sets each level through the settings API, waits 70 s, then reads the sampler's own
cost block every 10 s for 4 minutes and times one history call:

```
lab 2026-11-23T13:07:18Z, 4 min per level
Full: samples 24, sampler cpu_ms_per_sample mean 12.7, process CPU 0.21 s over 4 min (0.09 % of one core), rss 19.9 MB, ring now 0.51 MB, ring line 247 B (0.53 MB per 6 h at this host count), pf read 3.5 ms + parse 1.6 ms, hosts read every 10 s, states 52; history 1 h: 352 ms 95.1 kB, with every host: 286 ms 106.7 kB
Light: samples 24, sampler cpu_ms_per_sample mean 9.8, process CPU 0.16 s over 4 min (0.07 % of one core), rss 20.3 MB, ring now 0.51 MB, ring line 251 B (0.18 MB per 6 h at this host count), pf read 3.6 ms + parse 1.5 ms, hosts read every 30 s, states 47; history 1 h: 308 ms 95.1 kB, with every host: 267 ms 106.7 kB
left at Full (the default)

```

The history times are single calls through the GUI and only indicative.
With two hosts the levels differ by little; the difference grows with the host count, as the table above
shows. That is why the suggestion looks at the host count.

## The suggestion

The card reads cheap facts only:

- `sysctl` (cores, model, RAM, free + inactive pages);
- the sampler's own cost block (CPU per sample, pf states, ring size);
- the newest host list.

It suggests **Light** when any row passes its limit, and **Full** otherwise. It shows a table: this firewall's
value, where Full is suggested, where Light is, and which rows decided. One click applies the suggestion;
nothing changes by itself.

The limits are named constants in one place, `ASSESS_LIMITS` in
`src/opnsense/scripts/shaperinsight/sampler.py`. `sampler/assess` returns them with the values, and the page
prints what the API returns. So the page and this file cannot disagree; if one of them is changed, change
this table too.

| Row | Full suggested | Light suggested | How the limit was derived |
|---|---|---|---|
| CPU cores | from 2 | below 2 | Judgement: on one core the history reads compete with forwarding. |
| Available RAM | from 512 MB | below 512 MB | Judgement: below that, every MB in `/var/run` counts. |
| Hosts on the busiest pipe now | up to 100 | above 100 | Measured: one page poll with every host stored costs 235 ms at 100 hosts per pipe. That is 2.4 % of one core per open page at a 10 s refresh. At 1,000 hosts it is 1.8 s (18 %). The budget is 250 ms. |
| pf states | up to 3,000 | above 3,000 | Measured: 16 ms + 0.027 ms per state per read. 1.0.j was 16 ms + 0.022 ms (2026-11-22: 16 ms at 60 states, 150 ms at 6,231). 1.0.k also pairs each connection's LAN-side state with its WAN-side one, and its parse costs 1.24–1.28 × 1.0.j's: +0.005 ms per state, 108 → 138 ms at 6,000 states on the VM's Python 3.13 (2026-11-23). A 100 ms budget gives about 3,100. |
| Sampler CPU per 10 s step | up to 100 ms | above 100 ms | 1 % of one core. The sampler measures this itself (`cpu_ms_per_sample`). |
| Host list (6 h) with every host, estimated | up to 10 MB | above 10 MB | The estimate is the hosts per pipe now × 24 B × 2,160 lines. 24 B is a measured entry of 21.3–24.2 B, rounded up for longer addresses. 100 hosts on 2 pipes measured 9.3 MB. |
| That list as a share of available RAM | up to 5 % | above 5 % | Judgement: a convenience must not take a noticeable share of the RAM left. |

The health check S10 (status page) adds an info line when every host is stored and the suggestion is Light.
It also adds one when a pipe is over the 2,000-host ceiling (see below).

## Store every host: the ceiling

With the setting on, the sampler keeps every host of a pipe per 10 s step, up to `HOSTS_CEILING = 2000` a
pipe. A pipe with more hosts in a step falls back to the busiest 16 + the rest for that step. The record says
so (`over`). The page says so in three places:

- the status line;
- the note under G2 and G5;
- the S10 health line.

The 6 h ring stays bounded: 2,000 hosts × 2 pipes is about 190 MB, the top of the measured range.

The history API sends every host's series only when the page asks for them (`?all=1`). It asks only while G2
or G5 is on screen. Every other graph keeps the small top-N answer. A poll with `?since=` sends only the new
rows; the page joins them by host name (`delta.js`).

## Upgrading an existing install

The settings are new model fields with defaults, so an upgraded config gets them without a migration:

- **Store every host's history: on.** The ring holds every host from the first sample after the upgrade. The
  6 h window before that was recorded as top 16, so G2 and G5 say "partly limited" until those lines age
  out (6 h).
- **Read the hosts every: 10 s**, as before.
- **Refresh this page:** an install that saved its settings before 1.0.j has `poll_seconds` = 5 stored
  (the old default). The page treats it as 10 s, as before, and the level reads as Full.

An upgraded box therefore runs Full. On a firewall with many hosts, the suggestion and the S10 line then
point to Light. Nothing switches by itself: changing the defaults under a running box without asking was
judged worse than a visible suggestion.
