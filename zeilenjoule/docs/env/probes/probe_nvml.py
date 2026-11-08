"""Phase-0 probe: NVML energy counter + power sampling resolution (idle, 3 s)."""
import json, time, pynvml as n
n.nvmlInit(); h = n.nvmlDeviceGetHandleByIndex(0)
out = {"nvml_driver": n.nvmlSystemGetDriverVersion(), "nvml_version": n.nvmlSystemGetNVMLVersion()}
e0 = n.nvmlDeviceGetTotalEnergyConsumption(h); t0 = time.monotonic()
samples, changes, last = [], 0, None
while time.monotonic() - t0 < 3.0:
    p = n.nvmlDeviceGetPowerUsage(h); samples.append(p)
    changes += (last is not None and p != last); last = p; time.sleep(0.01)
e1 = n.nvmlDeviceGetTotalEnergyConsumption(h); dt = time.monotonic() - t0
out.update({"energy_counter_mJ_delta": e1 - e0, "counter_avg_W": round((e1 - e0) / 1000 / dt, 2),
            "sampled_avg_W": round(sum(samples) / len(samples) / 1000, 2), "polls": len(samples),
            "distinct_value_changes_in_3s": changes})
try:
    fv = n.nvmlDeviceGetFieldValues(h, [n.NVML_FI_DEV_POWER_INSTANT, n.NVML_FI_DEV_POWER_AVERAGE])
    out["field_power_instant_mW"] = fv[0].value.uiVal; out["field_power_avg_mW"] = fv[1].value.uiVal
except Exception as e: out["field_values_error"] = repr(e)
print(json.dumps(out, indent=1))
