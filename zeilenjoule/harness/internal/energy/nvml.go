package energy

import (
	"errors"
	"fmt"
	"sync"

	"github.com/NVIDIA/go-nvml/pkg/nvml"
)

// GPU is the narrow interface the sampler needs. NVMLGPU implements it; tests use fakes.
type GPU interface {
	EnergyMJ() (uint64, error) // board energy counter since driver load (mJ)
	PowerMW() (uint32, error)
	TempC() (uint32, error)
	MemUsedBytes() (uint64, error)
	UtilPct() (uint32, error)
}

// GPUInfo is static device information for the environment fingerprint.
type GPUInfo struct {
	Name           string  `json:"name"`
	UUID           string  `json:"uuid"`
	Driver         string  `json:"driver"`
	CUDADriver     string  `json:"cuda_driver"`
	PowerLimitW    float64 `json:"power_limit_w"`
	Persistence    bool    `json:"persistence"`
	MaxSMClockMHz  uint32  `json:"max_sm_clock_mhz"`
	MaxMemClockMHz uint32  `json:"max_mem_clock_mhz"`
	MemTotalBytes  uint64  `json:"mem_total_bytes"`
}

// NVMLGPU is a GPU backed by NVML (libnvidia-ml.so.1, loaded at runtime).
type NVMLGPU struct {
	dev nvml.Device
}

var nvmlInitOnce struct {
	sync.Mutex
	refs int
}

func nvmlErr(op string, ret nvml.Return) error {
	if ret == nvml.SUCCESS {
		return nil
	}
	return fmt.Errorf("nvml %s: %s", op, nvml.ErrorString(ret))
}

// OpenNVML initializes NVML and opens device index. Close must be called.
func OpenNVML(index int) (*NVMLGPU, error) {
	nvmlInitOnce.Lock()
	defer nvmlInitOnce.Unlock()
	if nvmlInitOnce.refs == 0 {
		if err := nvmlErr("init", nvml.Init()); err != nil {
			return nil, err
		}
	}
	dev, ret := nvml.DeviceGetHandleByIndex(index)
	if err := nvmlErr("device handle", ret); err != nil {
		if nvmlInitOnce.refs == 0 {
			_ = nvml.Shutdown()
		}
		return nil, err
	}
	nvmlInitOnce.refs++
	return &NVMLGPU{dev: dev}, nil
}

// Close releases the NVML reference.
func (g *NVMLGPU) Close() error {
	nvmlInitOnce.Lock()
	defer nvmlInitOnce.Unlock()
	nvmlInitOnce.refs--
	if nvmlInitOnce.refs == 0 {
		return nvmlErr("shutdown", nvml.Shutdown())
	}
	return nil
}

func (g *NVMLGPU) EnergyMJ() (uint64, error) {
	v, ret := g.dev.GetTotalEnergyConsumption()
	return v, nvmlErr("energy", ret)
}

func (g *NVMLGPU) PowerMW() (uint32, error) {
	v, ret := g.dev.GetPowerUsage()
	return v, nvmlErr("power", ret)
}

func (g *NVMLGPU) TempC() (uint32, error) {
	v, ret := g.dev.GetTemperature(nvml.TEMPERATURE_GPU)
	return v, nvmlErr("temperature", ret)
}

func (g *NVMLGPU) MemUsedBytes() (uint64, error) {
	m, ret := g.dev.GetMemoryInfo()
	return m.Used, nvmlErr("memory", ret)
}

func (g *NVMLGPU) UtilPct() (uint32, error) {
	u, ret := g.dev.GetUtilizationRates()
	return u.Gpu, nvmlErr("utilization", ret)
}

// Info collects static device information. Individual failures are joined, not fatal.
func (g *NVMLGPU) Info() (GPUInfo, error) {
	var info GPUInfo
	var errs []error
	collect := func(op string, ret nvml.Return) { errs = append(errs, nvmlErr(op, ret)) }
	var ret nvml.Return
	info.Name, ret = g.dev.GetName()
	collect("name", ret)
	info.UUID, ret = g.dev.GetUUID()
	collect("uuid", ret)
	info.Driver, ret = nvml.SystemGetDriverVersion()
	collect("driver", ret)
	cuda, ret := nvml.SystemGetCudaDriverVersion()
	collect("cuda driver", ret)
	info.CUDADriver = fmt.Sprintf("%d.%d", cuda/1000, (cuda%1000)/10)
	limit, ret := g.dev.GetEnforcedPowerLimit()
	collect("power limit", ret)
	info.PowerLimitW = float64(limit) / 1000
	pm, ret := g.dev.GetPersistenceMode()
	collect("persistence", ret)
	info.Persistence = pm == nvml.FEATURE_ENABLED
	info.MaxSMClockMHz, ret = g.dev.GetMaxClockInfo(nvml.CLOCK_SM)
	collect("max sm clock", ret)
	info.MaxMemClockMHz, ret = g.dev.GetMaxClockInfo(nvml.CLOCK_MEM)
	collect("max mem clock", ret)
	mem, ret := g.dev.GetMemoryInfo()
	collect("memory", ret)
	info.MemTotalBytes = mem.Total
	return info, errors.Join(errs...)
}
