# Security

This is an unofficial proof of concept with no maintainer on call and no release schedule.
It runs as root on a firewall (configd actions, an ipfw rule script, cron and boot hooks), so
treat a bug in it as a possible security problem.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab of this repository,
**Report a vulnerability**. Please do not open a public issue for something that could be
exploited. Say which file and version (`PLUGIN_VERSION` in `net/shaper-insight/Makefile`) you
looked at, how to reproduce it, and what an attacker gains.

There is no promise of a fix or a time frame. If the project looks abandoned, a fork that
takes the fix is welcome (see the banner in `README.md`).

## Scope notes

- The bufferbloat test sends traffic to third-party servers named on its page; that is
  intended, not a leak.
- The compiled-config page redacts public addresses, MAC addresses, host names and
  descriptions by default; a way to get such a value past the redactor is in scope.
- Findings that apply to OPNsense core or os-cake belong to those projects.
