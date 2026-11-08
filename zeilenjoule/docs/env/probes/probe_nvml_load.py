"""Phase-2 diagnostic: which NVML source is right under load? (calibration found counter vs integral +22 %)

Runs zj_embed.gpuburn for BURN s in a subprocess and polls at ~20 Hz:
energy counter (mJ), nvmlDeviceGetPowerUsage, field POWER_INSTANT, field POWER_AVERAGE.
Prints the energy from the counter and the trapezoid integral of each power reading.
"""
import json, subprocess, sys, time
import pynvml as n

BURN = float(sys.argv[1]) if len(sys.argv) > 1 else 20
n.nvmlInit(); h = n.nvmlDeviceGetHandleByIndex(0)
fields = [n.NVML_FI_DEV_POWER_INSTANT, n.NVML_FI_DEV_POWER_AVERAGE]
rows = []
def poll():
    fv = n.nvmlDeviceGetFieldValues(h, fields)
    rows.append((time.monotonic(), n.nvmlDeviceGetTotalEnergyConsumption(h), n.nvmlDeviceGetPowerUsage(h),
                 fv[0].value.uiVal, fv[1].value.uiVal))
proc = subprocess.Popen([sys.executable, "-m", "zj_embed.gpuburn", "--output", "/dev/null", "--seconds", str(BURN)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while proc.poll() is None:
    poll(); time.sleep(0.05)
for _ in range(40):  # 2 s tail
    poll(); time.sleep(0.05)
t0, e0 = rows[0][0], rows[0][1]
def integral(col):
    return sum((rows[i][col] + rows[i-1][col]) / 2 / 1000 * (rows[i][0] - rows[i-1][0]) for i in range(1, len(rows)))
dur = rows[-1][0] - t0
out = {"window_s": round(dur, 2), "polls": len(rows),
       "counter_J": (rows[-1][1] - e0) / 1000,
       "integral_GetPowerUsage_J": integral(2), "integral_FI_POWER_INSTANT_J": integral(3), "integral_FI_POWER_AVERAGE_J": integral(4),
       "counter_distinct_values": len({r[1] for r in rows}), "power_distinct_values": len({r[2] for r in rows}),
       "max_W": {"GetPowerUsage": max(r[2] for r in rows) / 1000, "instant": max(r[3] for r in rows) / 1000, "average": max(r[4] for r in rows) / 1000},
       "power_limit_W": n.nvmlDeviceGetEnforcedPowerLimit(h) / 1000}
# steady-state slice (middle 50 %): mean W per source
mid = [r for r in rows if t0 + dur * 0.25 <= r[0] <= t0 + dur * 0.75]
out["steady_mean_W"] = {
    "counter": (mid[-1][1] - mid[0][1]) / 1000 / (mid[-1][0] - mid[0][0]),
    "GetPowerUsage": sum(r[2] for r in mid) / len(mid) / 1000,
    "instant": sum(r[3] for r in mid) / len(mid) / 1000, "average": sum(r[4] for r in mid) / len(mid) / 1000}
print(json.dumps(out, indent=1))
