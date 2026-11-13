// Package benchstack starts the Phase 9 compose stack for measured harness runs (contestants e06_stack_go and
// e07_stack_load, ADR 0025/0026) and talks to it through the public HTTP API. The compose project
// "zeilenjoule-bench" uses fresh volumes and random secrets and never touches a normal `task up` stack.
package benchstack

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// Project is the compose project name of measured runs.
const Project = "zeilenjoule-bench"

// Mark prints a ZJMARK stage marker on stderr (spec/zjmark.md).
func Mark(stage, ev string) {
	fmt.Fprintf(os.Stderr, "ZJMARK {\"ev\":%q,\"stage\":%q}\n", ev, stage)
}

// Stage runs f between a start and an end marker.
func Stage(name string, f func() error) error {
	Mark(name, "start")
	defer Mark(name, "end")
	return f()
}

// Options configure the stack.
type Options struct {
	Compose string // compose file
	Port    int    // gateway port on 127.0.0.1
	Threads int    // parser threads of the worker
}

// Base is the gateway URL.
func (o Options) Base() string { return fmt.Sprintf("http://127.0.0.1:%d", o.Port) }

// Stack is one compose project with throw-away secrets.
type Stack struct {
	o   Options
	env []string
	Key string // API key of the run
}

func secret() string {
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// New prepares the environment; nothing is started.
func New(o Options) *Stack {
	key := secret()
	env := append(os.Environ(),
		"POSTGRES_PASSWORD="+secret(), "ZJ_READER_PASSWORD="+secret(), "ZJ_API_KEYS="+key,
		"GRAFANA_ADMIN_PASSWORD="+secret(),
		fmt.Sprintf("ZJ_UID=%d", os.Getuid()), fmt.Sprintf("ZJ_GID=%d", os.Getgid()),
		fmt.Sprintf("ZJ_GATEWAY_PORT=%d", o.Port),
		// the bench must measure the configured stack: no rate limiting of its own client
		"ZJ_RATE_LIMIT=1000", "ZJ_RATE_BURST=1000",
		fmt.Sprintf("ZJ_PARSER_THREADS=%d", o.Threads))
	return &Stack{o: o, env: env, Key: key}
}

// Compose runs `docker compose` for the bench project with profile gpu.
func (s *Stack) Compose(ctx context.Context, args ...string) ([]byte, error) {
	full := append([]string{"compose", "-p", Project, "-f", s.o.Compose, "--project-directory", filepath.Dir(s.o.Compose),
		"--profile", "gpu"}, args...)
	cmd := exec.CommandContext(ctx, "docker", full...) //nolint:gosec // fixed binary, arguments from flags of the operator
	cmd.Env = s.env
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return out.Bytes(), fmt.Errorf("docker %s: %w", strings.Join(full, " "), err)
	}
	return out.Bytes(), nil
}

// Down removes containers and volumes of the bench project.
func (s *Stack) Down(ctx context.Context) error {
	_, err := s.Compose(ctx, "down", "-v", "--remove-orphans", "--timeout", "30")
	return err
}

// Up starts the stack from existing images and waits for /readyz.
func (s *Stack) Up(ctx context.Context) error {
	base := s.o.Base()
	d := net.Dialer{Timeout: time.Second}
	if c, err := d.DialContext(ctx, "tcp", strings.TrimPrefix(base, "http://")); err == nil {
		_ = c.Close()
		return fmt.Errorf("%s is already in use: stop the running stack first (task down), it would also hold the GPU", base)
	}
	// images must exist (task images): building here would put a build into the measured run
	if _, err := s.Compose(ctx, "up", "-d", "--wait", "--no-build", "--pull", "never", "--quiet-pull"); err != nil {
		return err
	}
	return WaitReady(ctx, base)
}

// Psql runs one query as the owner and returns the unaligned output ("" on error).
func (s *Stack) Psql(ctx context.Context, q string) string {
	out, err := s.Compose(ctx, "exec", "-T", "postgres", "psql", "-U", "zeilenjoule", "-d", "zeilenjoule", "-Atc", q)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// ImageDigests maps container name → image reference@ID of the running project.
func (s *Stack) ImageDigests(ctx context.Context) map[string]string {
	out := map[string]string{}
	b, err := s.Compose(ctx, "images", "--format", "json")
	if err != nil {
		return out
	}
	var imgs []struct {
		ContainerName, Repository, Tag, ID string
	}
	if json.Unmarshal(b, &imgs) != nil {
		return out
	}
	for _, i := range imgs {
		ref := i.Repository
		if i.Tag != "" {
			ref += ":" + i.Tag
		}
		out[i.ContainerName] = ref + "@" + i.ID
	}
	return out
}

// PrintVersions is the versions_cmd: the images the stack would start, with their local IDs.
func PrintVersions(o Options, env string) error {
	s := New(o) // throw-away secrets: compose interpolates every variable even for `config`
	ctx := context.Background()
	b, err := s.Compose(ctx, "config", "--images")
	if err != nil {
		return err
	}
	imgs := map[string]string{}
	for _, ref := range strings.Fields(string(b)) {
		id, err := exec.CommandContext(ctx, "docker", "image", "inspect", "--format", "{{.Id}}", ref).Output() //nolint:gosec // image refs from the compose file
		if err != nil {
			return fmt.Errorf("image %s missing (task images): %w", ref, err)
		}
		imgs[ref] = strings.TrimSpace(string(id))
	}
	out, _ := json.Marshal(map[string]any{"env": env, "go": runtime.Version(), "images": imgs})
	fmt.Println(string(out))
	return nil
}

// WaitReady polls /readyz for up to 15 minutes (vLLM loads in 1–3 min).
func WaitReady(ctx context.Context, base string) error {
	deadline := time.Now().Add(15 * time.Minute)
	for time.Now().Before(deadline) {
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, base+"/readyz", nil)
		if r, err := http.DefaultClient.Do(req); err == nil {
			_ = r.Body.Close()
			if r.StatusCode == http.StatusOK {
				return nil
			}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
	return errors.New("gateway not ready after 15 min")
}

// ---------------------------------------------------------------- API client

// Client calls the gateway with the run's API key.
type Client struct {
	Base, Key string
	HTTP      *http.Client
}

// StatusError is a non-2xx answer of the gateway.
type StatusError struct {
	Method, Path string
	Code         int
	Body         string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("%s %s: %d %s", e.Method, e.Path, e.Code, e.Body)
}

// Do sends one request and decodes a 2xx JSON body into out (nil = discard).
func (c *Client) Do(ctx context.Context, method, path, ctype string, body io.Reader, out any) error {
	req, err := http.NewRequestWithContext(ctx, method, c.Base+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("X-API-Key", c.Key)
	if ctype != "" {
		req.Header.Set("Content-Type", ctype)
	}
	r, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = r.Body.Close() }()
	b, err := io.ReadAll(r.Body)
	if err != nil {
		return err
	}
	if r.StatusCode/100 != 2 {
		return &StatusError{Method: method, Path: path, Code: r.StatusCode, Body: string(bytes.TrimSpace(b))}
	}
	if out != nil {
		return json.Unmarshal(b, out)
	}
	return nil
}

// Job is the ingest job state (GET /v1/jobs/{id}).
type Job struct {
	ID    int64  `json:"id"`
	State string `json:"state"`
	Rows  int64  `json:"rows"`
	Error string `json:"error"`
}

// Ingest uploads the raw CSV and polls the job until it is done or failed.
func (c *Client) Ingest(ctx context.Context, path string) (Job, error) {
	f, err := os.Open(path) //nolint:gosec // the harness passes the input path
	if err != nil {
		return Job{}, err
	}
	defer func() { _ = f.Close() }()
	var j Job
	if err := c.Do(ctx, http.MethodPost, "/v1/ingest", "text/csv", f, &j); err != nil {
		return j, err
	}
	for {
		select {
		case <-ctx.Done():
			return j, ctx.Err()
		case <-time.After(2 * time.Second):
		}
		if err := c.Do(ctx, http.MethodGet, "/v1/jobs/"+strconv.FormatInt(j.ID, 10), "", nil, &j); err != nil {
			return j, err
		}
		switch j.State {
		case "done":
			return j, nil
		case "failed":
			return j, fmt.Errorf("ingest job %d failed: %s", j.ID, j.Error)
		}
	}
}

// Question is one Track E question (data/track_e/<scale>/questions.jsonl, text only).
type Question struct {
	QID  string `json:"qid"`
	Text string `json:"text"`
}

// ReadQuestions reads a questions.jsonl file.
func ReadQuestions(path string) ([]Question, error) {
	f, err := os.Open(path) //nolint:gosec // repo-relative data path
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()
	var qs []Question
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		if strings.TrimSpace(sc.Text()) == "" {
			continue
		}
		var q Question
		if err := json.Unmarshal(sc.Bytes(), &q); err != nil {
			return nil, err
		}
		qs = append(qs, q)
	}
	return qs, sc.Err()
}

// QuestionsFor returns the questions of the scale the input CSV belongs to (…/<scale>/stoermeldungen.cp1252.csv).
func QuestionsFor(input string) ([]Question, error) {
	scale := filepath.Base(filepath.Dir(input))
	return ReadQuestions(filepath.Join("data", "track_e", scale, "questions.jsonl"))
}
