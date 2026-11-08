package main

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUsageAndUnknownCommand(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run(context.Background(), nil, &out, &errb); code != 2 || !strings.Contains(errb.String(), "usage") {
		t.Fatalf("no args: code=%d stderr=%q", code, errb.String())
	}
	errb.Reset()
	if code := run(context.Background(), []string{"frobnicate"}, &out, &errb); code != 2 || !strings.Contains(errb.String(), "unknown command") {
		t.Fatalf("unknown: code=%d stderr=%q", code, errb.String())
	}
}

func TestVersion(t *testing.T) {
	var out bytes.Buffer
	if code := run(context.Background(), []string{"version"}, &out, &out); code != 0 || !strings.HasPrefix(out.String(), "zj 0.2.0") {
		t.Fatalf("code=%d out=%q", code, out.String())
	}
}

func TestFindRepoRoot(t *testing.T) {
	root := t.TempDir()
	deep := filepath.Join(root, "a", "b")
	if err := os.MkdirAll(filepath.Join(root, "config"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(deep, 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "config", "bench.yaml"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := findRepoRoot(deep)
	if err != nil || got != root {
		t.Fatalf("findRepoRoot = %q, %v; want %q", got, err, root)
	}
	if _, err := findRepoRoot(t.TempDir()); err == nil {
		t.Fatal("expected error outside a repo")
	}
}
