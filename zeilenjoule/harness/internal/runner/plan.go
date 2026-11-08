package runner

import (
	"fmt"
	"math/rand/v2"
	"slices"

	"zeilenjoule/harness/internal/config"
)

// Item is one scheduled run.
type Item struct {
	Contestant string `json:"contestant"`
	Scale      string `json:"scale"`
	Rep        int    `json:"rep"` // 1-based within warmups resp. measured reps
	Warmup     bool   `json:"warmup"`
}

// Key identifies an item for resume.
func (it Item) Key() string {
	return fmt.Sprintf("%s|%s|%t|%d", it.Contestant, it.Scale, it.Warmup, it.Rep)
}

// MakePlan builds the run order (PLAN §5.2): scales ascending by rows; per scale all warmups
// first, then rounds of measured reps. With interleave each round is a fresh seeded permutation
// (A1 B1 C1 / B2 A2 C2 …), which spreads thermal drift across contestants; otherwise
// contestant-major. Contestants are only scheduled on scales they declare.
func MakePlan(cs []config.Contestant, scales []string, scaleRows map[string]int, reps, warmup int, interleave bool, seed int64) []Item {
	order := slices.Clone(scales)
	slices.SortStableFunc(order, func(a, b string) int { return scaleRows[a] - scaleRows[b] })
	var plan []Item
	for si, sc := range order {
		var ids []string
		for _, c := range cs {
			if slices.Contains(c.Scales, sc) {
				ids = append(ids, c.ID)
			}
		}
		if len(ids) == 0 {
			continue
		}
		rng := rand.New(rand.NewPCG(uint64(seed), uint64(si)+1)) //nolint:gosec // G404: reproducible order, not security
		perm := func() []string {
			p := slices.Clone(ids)
			rng.Shuffle(len(p), func(i, j int) { p[i], p[j] = p[j], p[i] })
			return p
		}
		if !interleave {
			for _, id := range ids {
				for w := 1; w <= warmup; w++ {
					plan = append(plan, Item{id, sc, w, true})
				}
				for r := 1; r <= reps; r++ {
					plan = append(plan, Item{id, sc, r, false})
				}
			}
			continue
		}
		for w := 1; w <= warmup; w++ {
			for _, id := range perm() {
				plan = append(plan, Item{id, sc, w, true})
			}
		}
		for r := 1; r <= reps; r++ {
			for _, id := range perm() {
				plan = append(plan, Item{id, sc, r, false})
			}
		}
	}
	return plan
}
