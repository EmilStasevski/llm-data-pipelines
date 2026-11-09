#!/usr/bin/env bash
# Quiet box for energy measurements (CLAUDE.md rule 7, checklist C1/C5/C6).
#
#   scripts/quiet_box.sh prepare            # once: persistent RAPL read access (udev rule, checklist C1)
#   scripts/quiet_box.sh measure [zj args]  # stop everything → measure → ALWAYS restore (also on Ctrl-C/errors)
#   scripts/quiet_box.sh stop | restore     # manual control
#   scripts/quiet_box.sh status             # what is running / would be stopped
#   DRY_RUN=1 scripts/quiet_box.sh measure  # print the commands only
#
# Default measurement: zj run --track A --scale S,M,L (≈ 40 min, plan for ≤ 60 min).
# Stops: k3s incl. all pods (k3s-killall.sh), Ollama, all running Docker containers, Google Chrome.
# Chrome gets SIGTERM (graceful; tabs can be restored on next start). It is NOT restarted automatically.
# Not touched: the desktop (gnome-shell/Xorg), the CPU governor (ADR 0003), the GPU power settings.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$REPO/logs/quiet_box.state"
LOG="$REPO/logs/quiet_box_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$REPO/logs"

run() { # run or print a command
  if [[ "${DRY_RUN:-0}" == 1 ]]; then echo "DRY: $*" >&2; else "$@"; fi
}
say() { echo "[quiet_box] $*" | tee -a "$LOG" >&2; }

rapl_readable() { cat /sys/class/powercap/intel-rapl:0/energy_uj >/dev/null 2>&1; }

prepare() {
  local rule=/etc/udev/rules.d/99-zeilenjoule-rapl.rules
  # match add AND change: `udevadm trigger` sends "change" by default, the boot coldplug sends "add"
  local want='SUBSYSTEM=="powercap", ACTION=="add|change", RUN+="/bin/chmod o+r /sys%p/energy_uj"'
  if [[ "$(cat "$rule" 2>/dev/null)" != "$want" ]]; then
    say "installing $rule (persistent world-readable RAPL energy counters; minor power side channel, see C1)"
    echo "$want" | run sudo tee "$rule" >/dev/null
    run sudo udevadm control --reload
  fi
  run sudo udevadm trigger --action=add --subsystem-match=powercap
  run sudo udevadm settle
  if [[ "${DRY_RUN:-0}" == 1 ]] || rapl_readable; then say "RAPL readable"; else
    say "RAPL still NOT readable; permissions: $(ls -l /sys/class/powercap/intel-rapl:0/energy_uj)"; return 1
  fi
}

status() {
  echo "k3s:     $(systemctl is-active k3s 2>/dev/null || true)"
  echo "ollama:  $(systemctl is-active ollama 2>/dev/null || true)"
  echo "docker:  $(docker ps --format '{{.Names}}' | tr '\n' ' ')"
  echo "chrome:  $(pgrep -fc '^/opt/google/chrome/chrome' || true) processes"
  echo "RAPL:    $(rapl_readable && echo readable || echo NOT readable)"
  echo "top CPU:"; ps -eo pcpu,comm --sort=-pcpu | sed -n 2,6p
}

stop_all() {
  [[ -f $STATE ]] && { say "state file exists ($STATE): a previous stop was not restored; run 'restore' first"; return 1; }
  {
    echo "k3s=$(systemctl is-active k3s 2>/dev/null || true)"
    echo "ollama=$(systemctl is-active ollama 2>/dev/null || true)"
    # our own benchmark containers (zj-*) are not services: contestants start and remove them themselves
    echo "containers=$(docker ps --format '{{.Names}}' | grep -v '^zj-' | tr '\n' ' ')"
  } > "$STATE"
  local stray; stray=$(docker ps --format '{{.Names}}' | grep '^zj-' | tr '\n' ' ' || true)
  [[ -n "${stray// }" ]] && { say "removing leftover benchmark containers: $stray"; run docker rm -f $stray >/dev/null; }
  say "saved state → $STATE"; sed 's/^/  /' "$STATE" >&2
  if pgrep -f '^/opt/google/chrome/chrome' >/dev/null; then
    say "closing Google Chrome (SIGTERM, graceful)"
    run pkill -TERM -f '^/opt/google/chrome/chrome' || true
    for _ in $(seq 20); do pgrep -f '^/opt/google/chrome/chrome' >/dev/null || break; sleep 0.5; done
  fi
  local containers; containers=$(grep '^containers=' "$STATE" | cut -d= -f2-)
  [[ -n "${containers// }" ]] && { say "stopping containers: $containers"; run docker stop $containers >/dev/null; }
  if grep -q '^ollama=active' "$STATE"; then say "stopping ollama"; run sudo systemctl stop ollama; fi
  if grep -q '^k3s=active' "$STATE"; then
    say "stopping k3s and all pods"
    if [[ -x /usr/local/bin/k3s-killall.sh ]]; then run sudo /usr/local/bin/k3s-killall.sh >/dev/null 2>&1; else run sudo systemctl stop k3s; fi
  fi
  say "remaining top CPU consumers:"; ps -eo pcpu,comm --sort=-pcpu | sed -n 2,6p | tee -a "$LOG" >&2
}

restore() {
  [[ -f $STATE ]] || { say "nothing to restore"; return 0; }
  if grep -q '^k3s=active' "$STATE"; then say "starting k3s"; run sudo systemctl start k3s; fi
  if grep -q '^ollama=active' "$STATE"; then say "starting ollama"; run sudo systemctl start ollama; fi
  local containers; containers=$(grep '^containers=' "$STATE" | cut -d= -f2-)
  if [[ -n "${containers// }" ]]; then
    say "starting containers: $containers"
    for c in $containers; do  # one at a time: a container that no longer exists must not block the rest
      [[ $c == zj-* ]] && continue
      run docker start "$c" >/dev/null || say "  could not start $c (removed?), skipped"
    done
  fi
  run rm -f "$STATE"
  say "restored (Chrome is not restarted automatically)"
}

measure() {
  local args=("$@")
  [[ ${#args[@]} -gt 0 ]] || args=(run --track A --scale S,M,L)
  if [[ "${DRY_RUN:-0}" != 1 ]] && ! rapl_readable; then
    say "RAPL not readable → run 'scripts/quiet_box.sh prepare' first (CPU energy would be missing)"; return 1
  fi
  run sudo -v  # ask for the password once, now
  # keep sudo alive so the automatic restore at the end does not stall on a password prompt
  if [[ "${DRY_RUN:-0}" != 1 ]]; then
    ( while kill -0 $$ 2>/dev/null; do sudo -n true 2>/dev/null; sleep 60; done ) &
  fi
  trap 'say "restoring services"; restore' EXIT INT TERM
  stop_all
  say "building zj, then: zj ${args[*]}  (log: $LOG)"
  run "$HOME/go/bin/task" build:harness >/dev/null
  run "$REPO/bin/zj" doctor | tee -a "$LOG" >&2 || true
  local t0=$SECONDS rc=0
  run "$REPO/bin/zj" "${args[@]}" 2>&1 | tee -a "$LOG" || rc=$?
  say "measurement finished after $(( (SECONDS - t0) / 60 )) min (exit $rc)"
  return "$rc"
}

cd "$REPO"
case "${1:-}" in
  prepare) prepare ;;
  status) status ;;
  stop) stop_all ;;
  restore) restore ;;
  measure) shift; measure "$@" ;;
  *) sed -n 2,13p "$0"; exit 2 ;;
esac
