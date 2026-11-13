// Package sqlguard checks router-generated SQL before it reaches PostgreSQL (spec/track_e.md §3, ADR 0021).
//
// The statement is parsed with PostgreSQL's own parser (libpg_query via pg_query_go). It passes only if it is
// exactly one SELECT (UNION/INTERSECT/EXCEPT of SELECTs allowed), every relation it reads is the view
// `meldungen` (or a CTE defined in the same statement), it has no INTO, no locking clause, and calls none of
// the functions below. The database executes it as a SELECT-only role with statement_timeout and a read-only
// transaction as the second line of defense.
package sqlguard

import (
	"encoding/json"
	"fmt"
	"strings"

	pg "github.com/pganalyze/pg_query_go/v6"
)

// View is the only relation router SQL may read.
const View = "meldungen"

// forbiddenFuncs read files, sleep, touch settings or reach other servers.
var forbiddenFuncs = map[string]bool{
	"pg_read_file": true, "pg_read_binary_file": true, "pg_ls_dir": true, "pg_stat_file": true,
	"pg_sleep": true, "pg_sleep_for": true, "pg_sleep_until": true, "lo_import": true, "lo_export": true,
	"lo_get": true, "dblink": true, "dblink_exec": true, "current_setting": true, "set_config": true,
	"pg_terminate_backend": true, "pg_cancel_backend": true, "pg_reload_conf": true, "query_to_xml": true,
	"pg_advisory_lock": true, "txid_current": true, "nextval": true, "setval": true,
}

// Check returns nil when sql is safe to run on the view, otherwise an error naming the first violation.
func Check(sql string) error {
	tree, err := pg.Parse(sql)
	if err != nil {
		return fmt.Errorf("parse: %w", err)
	}
	if n := len(tree.GetStmts()); n != 1 {
		return fmt.Errorf("%d statements, want exactly 1", n)
	}
	sel := tree.GetStmts()[0].GetStmt().GetSelectStmt()
	if sel == nil {
		return fmt.Errorf("not a SELECT statement")
	}
	// Walk the JSON form of the parse tree: generic, so new node types cannot slip past a hand-written visitor.
	raw, err := pg.ParseToJSON(sql)
	if err != nil {
		return fmt.Errorf("parse: %w", err)
	}
	var doc any
	if err := json.Unmarshal([]byte(raw), &doc); err != nil {
		return fmt.Errorf("parse tree: %w", err)
	}
	ctes := map[string]bool{}
	collectCTEs(doc, ctes)
	return walk(doc, ctes)
}

func collectCTEs(n any, out map[string]bool) {
	switch v := n.(type) {
	case map[string]any:
		if cte, ok := v["CommonTableExpr"].(map[string]any); ok {
			if name, ok := cte["ctename"].(string); ok {
				out[strings.ToLower(name)] = true
			}
		}
		for _, c := range v {
			collectCTEs(c, out)
		}
	case []any:
		for _, c := range v {
			collectCTEs(c, out)
		}
	}
}

func walk(n any, ctes map[string]bool) error {
	switch v := n.(type) {
	case map[string]any:
		for key, child := range v {
			if err := node(key, child, ctes); err != nil {
				return err
			}
			if err := walk(child, ctes); err != nil {
				return err
			}
		}
	case []any:
		for _, c := range v {
			if err := walk(c, ctes); err != nil {
				return err
			}
		}
	}
	return nil
}

func node(key string, child any, ctes map[string]bool) error {
	m, _ := child.(map[string]any)
	switch key {
	case "InsertStmt", "UpdateStmt", "DeleteStmt", "MergeStmt", "CopyStmt", "CreateStmt", "DropStmt",
		"TruncateStmt", "VariableSetStmt", "DoStmt", "CallStmt", "LockStmt":
		return fmt.Errorf("forbidden statement %s", key)
	case "intoClause":
		return fmt.Errorf("SELECT … INTO is not allowed")
	case "lockingClause":
		return fmt.Errorf("FOR UPDATE/SHARE is not allowed")
	case "RangeVar":
		if s, ok := m["schemaname"].(string); ok && s != "" {
			return fmt.Errorf("qualified relation %s.%v", s, m["relname"])
		}
		name, _ := m["relname"].(string)
		if !strings.EqualFold(name, View) && !ctes[strings.ToLower(name)] {
			return fmt.Errorf("relation %q is not the view %s", name, View)
		}
	case "RangeFunction", "RangeTableFunc":
		return fmt.Errorf("table functions are not allowed")
	case "FuncCall":
		names, _ := m["funcname"].([]any) // e.g. [pg_catalog, pg_sleep]: every element is checked
		for _, x := range names {
			s, _ := x.(map[string]any)["String"].(map[string]any)
			if fn, ok := s["sval"].(string); ok && forbiddenFuncs[strings.ToLower(fn)] {
				return fmt.Errorf("function %s is not allowed", fn)
			}
		}
	}
	return nil
}
