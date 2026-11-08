// Package procs measures a process tree (RSS sum) cheaply via /proc/<pid>/task/<tid>/children,
// avoiding a scan of every PID on the system while a benchmark runs.
package procs

import (
	"bufio"
	"bytes"
	"path/filepath"
	"strconv"
	"strings"
)

// ReadFile and Glob are injected for tests.
type FS struct {
	ReadFile func(string) ([]byte, error)
	Glob     func(string) ([]string, error)
}

// Tree returns root and all live descendants (breadth-first, deduplicated).
func (f FS) Tree(root int) []int {
	seen := map[int]bool{root: true}
	queue, out := []int{root}, []int{}
	for len(queue) > 0 {
		pid := queue[0]
		queue = queue[1:]
		out = append(out, pid)
		tasks, _ := f.Glob("/proc/" + strconv.Itoa(pid) + "/task/*/children")
		for _, tf := range tasks {
			b, err := f.ReadFile(tf)
			if err != nil {
				continue
			}
			for _, s := range strings.Fields(string(b)) {
				child, err := strconv.Atoi(s)
				if err == nil && !seen[child] {
					seen[child] = true
					queue = append(queue, child)
				}
			}
		}
	}
	return out
}

// RSSBytes returns VmRSS of one process (0 if it is gone or a kernel thread).
func (f FS) RSSBytes(pid int) uint64 {
	b, err := f.ReadFile(filepath.Join("/proc", strconv.Itoa(pid), "status"))
	if err != nil {
		return 0
	}
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		if v, ok := strings.CutPrefix(sc.Text(), "VmRSS:"); ok {
			fields := strings.Fields(v) // "123456 kB"
			if len(fields) >= 1 {
				kb, err := strconv.ParseUint(fields[0], 10, 64)
				if err == nil {
					return kb * 1024
				}
			}
		}
	}
	return 0
}

// TreeRSS returns the summed RSS of root and its descendants, and the process count.
// Shared pages are counted once per process (an upper bound), which is standard for RSS sums.
func (f FS) TreeRSS(root int) (bytes uint64, nprocs int) {
	pids := f.Tree(root)
	for _, p := range pids {
		bytes += f.RSSBytes(p)
	}
	return bytes, len(pids)
}
