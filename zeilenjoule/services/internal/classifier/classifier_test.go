package classifier

import (
	"math/rand/v2"
	"testing"
)

// three well separated Gaussian clusters in 8 dimensions must be learned almost perfectly
func TestFitSeparable(t *testing.T) {
	rng := rand.New(rand.NewPCG(1, 2))
	const dim, per = 8, 60
	var x [][]float32
	var y []int
	for c := range 3 {
		for range per {
			v := make([]float32, dim)
			for i := range v {
				v[i] = float32(rng.NormFloat64() * 0.3)
			}
			v[c] += 2
			x = append(x, v)
			y = append(y, c)
		}
	}
	m, err := Fit(x, y, []string{"a", "b", "c"}, Options{L2: 1e-4, Iters: 200, LR: 0.05})
	if err != nil {
		t.Fatal(err)
	}
	if acc := m.Accuracy(x, y); acc < 0.98 {
		t.Fatalf("accuracy %.3f < 0.98", acc)
	}
	if len(m.W) != (dim+1)*3 || m.Dim != dim {
		t.Fatalf("shape: %d weights, dim %d", len(m.W), m.Dim)
	}
}

func TestFitRejectsDegenerate(t *testing.T) {
	if _, err := Fit(nil, nil, []string{"a", "b"}, Defaults); err == nil {
		t.Fatal("empty input accepted")
	}
	if _, err := Fit([][]float32{{1}}, []int{0}, []string{"a"}, Defaults); err == nil {
		t.Fatal("single class accepted")
	}
}
