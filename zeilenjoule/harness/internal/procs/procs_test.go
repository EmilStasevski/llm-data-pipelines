package procs

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func fake() FS {
	files := map[string]string{
		"/proc/10/task/10/children": "11 12",
		"/proc/10/task/13/children": "14",    // a child spawned by a second thread
		"/proc/11/task/11/children": "15 10", // cycle back to root must not loop
		"/proc/12/task/12/children": "",
		"/proc/10/status":           "Name:\tuv\nVmRSS:\t   1000 kB\n",
		"/proc/11/status":           "Name:\tpython\nVmRSS:\t 200000 kB\n",
		"/proc/12/status":           "Name:\tkthread\n",
		"/proc/14/status":           "VmRSS: 50 kB\n",
		"/proc/15/status":           "VmRSS: 3 kB\n",
	}
	return FS{
		ReadFile: func(p string) ([]byte, error) {
			if s, ok := files[p]; ok {
				return []byte(s), nil
			}
			return nil, fs.ErrNotExist
		},
		Glob: func(pattern string) ([]string, error) {
			var out []string
			for p := range files {
				if ok, _ := filepath.Match(pattern, p); ok {
					out = append(out, p)
				}
			}
			return out, nil
		},
	}
}

func TestTreeRSS(t *testing.T) {
	b, n := fake().TreeRSS(10)
	if n != 5 {
		t.Errorf("nprocs = %d, want 5 (10,11,12,14,15; cycle back to 10 not double-counted)", n)
	}
	if want := uint64(1000+200000+50+3) * 1024; b != want {
		t.Errorf("rss = %d, want %d", b, want)
	}
}

func TestTreeRSSGoneProcess(t *testing.T) {
	if b, n := fake().TreeRSS(999); b != 0 || n != 1 {
		t.Errorf("gone process: rss=%d n=%d", b, n)
	}
}

// Live: a shell with a sleeping child must show up as a tree of ≥ 2 processes.
func TestLiveTree(t *testing.T) {
	cmd := exec.CommandContext(t.Context(), "sh", "-c", "sleep 5 & sleep 5; wait")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	sys := FS{ReadFile: os.ReadFile, Glob: filepath.Glob}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if b, n := sys.TreeRSS(cmd.Process.Pid); n >= 3 && b > 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("did not observe sh + 2 sleep children")
}
