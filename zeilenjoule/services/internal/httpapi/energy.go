package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"zeilenjoule/services/internal/api"
)

// Energy reads the Prometheus recording rules zj:joules_per_ask:1m and zj:energy_watts:rate1m
// (deploy/monitoring/rules). The gateway has no energy sensor of its own: without Prometheus there is no value,
// and the answer says why instead of inventing one (rule 3).
func (s *Server) Energy(ctx context.Context, _ api.EnergyRequestObject) (api.EnergyResponseObject, error) {
	if s.Prometheus == "" {
		return api.Energy200JSONResponse{Source: "not configured (ZJ_PROMETHEUS_URL)"}, nil
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	jpa, err := s.promScalar(ctx, "zj:joules_per_ask:1m")
	if err != nil {
		return api.Energy200JSONResponse{Source: "prometheus unreachable"}, nil //nolint:nilerr // reported in the body
	}
	w, err := s.promScalar(ctx, "zj:energy_watts:rate1m")
	if err != nil {
		return api.Energy200JSONResponse{Source: "prometheus unreachable"}, nil //nolint:nilerr // reported in the body
	}
	return api.Energy200JSONResponse{JoulesPerAsk: jpa, Watts: w, Source: "prometheus"}, nil
}

// promScalar runs an instant query; nil when the series has no sample (e.g. no question in the last minute).
func (s *Server) promScalar(ctx context.Context, q string) (*float32, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.Prometheus+"/api/v1/query?query="+url.QueryEscape(q), nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("prometheus: HTTP %d", resp.StatusCode)
	}
	var r struct {
		Status string `json:"status"`
		Data   struct {
			Result []struct {
				Value [2]any `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return nil, fmt.Errorf("prometheus: decode: %w", err)
	}
	if r.Status != "success" {
		return nil, fmt.Errorf("prometheus: status %q", r.Status)
	}
	if len(r.Data.Result) == 0 {
		return nil, nil
	}
	str, _ := r.Data.Result[0].Value[1].(string)
	v, err := strconv.ParseFloat(str, 32)
	if err != nil {
		return nil, fmt.Errorf("prometheus: value %q", str)
	}
	f := float32(v)
	return &f, nil
}
