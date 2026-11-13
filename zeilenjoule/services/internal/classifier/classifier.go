// Package classifier is the schadensart distillation of ADR 0020 in Go: multinomial logistic regression on
// bge-m3 vectors, full-batch Adam (lr 0.05, β 0.9/0.999), L2 1e-4, 400 iterations, bias as a constant input —
// the same procedure as zj_e2e.pipeline.softmax_fit.
package classifier

import (
	"errors"
	"math"
	"runtime"
	"sync"
)

// Model holds W with (Dim+1) rows × len(Classes) columns, row-major; the last row is the bias.
type Model struct {
	Classes []string
	Dim     int
	W       []float32
}

// Options of the fit.
type Options struct {
	L2    float64
	Iters int
	LR    float64
}

// Defaults are the benchmark's values.
var Defaults = Options{L2: 1e-4, Iters: 400, LR: 0.05}

// Fit trains on x (n vectors of length dim) with labels y (class indices).
func Fit(x [][]float32, y []int, classes []string, o Options) (*Model, error) {
	n, c := len(x), len(classes)
	if n == 0 || n != len(y) || c < 2 {
		return nil, errors.New("classifier: need ≥ 1 example and ≥ 2 classes")
	}
	d := len(x[0]) + 1
	w := make([]float64, d*c)
	m := make([]float64, d*c)
	v := make([]float64, d*c)
	for t := 1; t <= o.Iters; t++ {
		g := gradient(x, y, w, d, c)
		for i := range g {
			g[i] = g[i]/float64(n) + 2*o.L2*w[i]
			m[i] = 0.9*m[i] + 0.1*g[i]
			v[i] = 0.999*v[i] + 0.001*g[i]*g[i]
			mh := m[i] / (1 - math.Pow(0.9, float64(t)))
			vh := v[i] / (1 - math.Pow(0.999, float64(t)))
			w[i] -= o.LR * mh / (math.Sqrt(vh) + 1e-8)
		}
	}
	out := &Model{Classes: classes, Dim: d - 1, W: make([]float32, len(w))}
	for i, x := range w {
		out.W[i] = float32(x)
	}
	return out, nil
}

// gradient returns Σ_rows xbᵀ(softmax(xb·W) − onehot), split over CPU cores.
func gradient(x [][]float32, y []int, w []float64, d, c int) []float64 {
	workers := runtime.GOMAXPROCS(0)
	parts := make([][]float64, workers)
	var wg sync.WaitGroup
	chunk := (len(x) + workers - 1) / workers
	for p := range workers {
		lo, hi := p*chunk, min((p+1)*chunk, len(x))
		if lo >= hi {
			continue
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			g := make([]float64, d*c)
			z := make([]float64, c)
			for r := lo; r < hi; r++ {
				logits(x[r], w, d, c, z)
				softmax(z)
				z[y[r]] -= 1
				for i, xi := range x[r] {
					row := g[i*c : (i+1)*c]
					for k := range c {
						row[k] += float64(xi) * z[k]
					}
				}
				bias := g[(d-1)*c:]
				for k := range c {
					bias[k] += z[k]
				}
			}
			parts[p] = g
		}()
	}
	wg.Wait()
	g := make([]float64, d*c)
	for _, p := range parts {
		for i, v := range p {
			g[i] += v
		}
	}
	return g
}

func logits(x []float32, w []float64, d, c int, z []float64) {
	copy(z, w[(d-1)*c:d*c])
	for i, xi := range x {
		row := w[i*c : (i+1)*c]
		for k := range c {
			z[k] += float64(xi) * row[k]
		}
	}
}

func softmax(z []float64) {
	mx := math.Inf(-1)
	for _, v := range z {
		mx = math.Max(mx, v)
	}
	var s float64
	for i, v := range z {
		z[i] = math.Exp(v - mx)
		s += z[i]
	}
	for i := range z {
		z[i] /= s
	}
}

// Predict returns the arg-max class index per vector.
func (m *Model) Predict(x [][]float32) []int {
	c := len(m.Classes)
	out := make([]int, len(x))
	z := make([]float32, c)
	for r, v := range x {
		copy(z, m.W[m.Dim*c:])
		for i, xi := range v {
			row := m.W[i*c : (i+1)*c]
			for k := range c {
				z[k] += xi * row[k]
			}
		}
		best := 0
		for k := 1; k < c; k++ {
			if z[k] > z[best] {
				best = k
			}
		}
		out[r] = best
	}
	return out
}

// Accuracy of the model on labeled vectors.
func (m *Model) Accuracy(x [][]float32, y []int) float64 {
	if len(x) == 0 {
		return math.NaN()
	}
	ok := 0
	for i, p := range m.Predict(x) {
		if p == y[i] {
			ok++
		}
	}
	return float64(ok) / float64(len(x))
}
