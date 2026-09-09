// Package report owns the Go projection of the cross-harness verdict wire format.
package report

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type Status string

const (
	StatusPass    Status = "pass"
	StatusFail    Status = "fail"
	StatusError   Status = "error"
	StatusSkipped Status = "skipped"
)

type Metric struct {
	Value any    `json:"value"`
	Unit  string `json:"unit,omitempty"`
}

type CaseVerdict struct {
	CaseID  string            `json:"case_id"`
	ArmID   string            `json:"arm_id,omitempty"`
	Status  Status            `json:"status"`
	Reason  string            `json:"reason,omitempty"`
	Facets  map[string]string `json:"facets,omitempty"`
	Metrics map[string]Metric `json:"metrics,omitempty"`
}

type RunVerdict struct {
	SchemaVersion int               `json:"schema_version"`
	Harness       string            `json:"harness"`
	Scope         string            `json:"scope"`
	RunID         string            `json:"run_id"`
	Status        Status            `json:"status"`
	Reason        string            `json:"reason,omitempty"`
	Cases         []CaseVerdict     `json:"cases,omitempty"`
	ArtifactPaths map[string]string `json:"artifact_paths,omitempty"`
	CreatedAt     string            `json:"created_at,omitempty"`
}

func BuildRunVerdict(scope, runID string, cases []CaseVerdict) RunVerdict {
	status := rollup(cases)
	return RunVerdict{
		SchemaVersion: 1,
		Harness:       "e2e",
		Scope:         scope,
		RunID:         runID,
		Status:        status,
		Reason:        rollupReason(cases, status),
		Cases:         cases,
		CreatedAt:     time.Now().UTC().Format(time.RFC3339),
	}
}

func WriteVerdict(runDir string, verdict RunVerdict) (string, error) {
	if err := os.MkdirAll(runDir, 0o755); err != nil {
		return "", fmt.Errorf("create verdict run dir %s: %w", runDir, err)
	}
	data, err := json.MarshalIndent(verdict, "", "  ")
	if err != nil {
		return "", fmt.Errorf("marshal verdict: %w", err)
	}
	path := filepath.Join(runDir, "verdict.json")
	data = append(data, '\n')
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return "", fmt.Errorf("write verdict %s: %w", path, err)
	}
	return path, nil
}

func rollup(cases []CaseVerdict) Status {
	if len(cases) == 0 {
		return StatusSkipped
	}
	hasPass := false
	hasFail := false
	hasSkipped := false
	for _, c := range cases {
		switch c.Status {
		case StatusError:
			return StatusError
		case StatusFail:
			hasFail = true
		case StatusPass:
			hasPass = true
		case StatusSkipped:
			hasSkipped = true
		}
	}
	if hasFail {
		return StatusFail
	}
	if hasPass {
		return StatusPass
	}
	if hasSkipped {
		return StatusSkipped
	}
	return StatusSkipped
}

func rollupReason(cases []CaseVerdict, status Status) string {
	if status == StatusPass {
		return ""
	}
	counts := map[Status]int{}
	for _, c := range cases {
		counts[c.Status]++
	}
	parts := make([]string, 0, 3)
	for _, item := range []Status{StatusFail, StatusError, StatusSkipped} {
		if counts[item] > 0 {
			parts = append(parts, fmt.Sprintf("%d %s", counts[item], item))
		}
	}
	headline := strings.Join(parts, ", ")
	if headline == "" {
		headline = string(status)
	}
	for _, c := range cases {
		if c.Status == status {
			detail := c.CaseID
			if c.Reason == "" {
				return fmt.Sprintf("%s; first %s — %s", headline, status, detail)
			}
			detail += ": " + c.Reason
			return fmt.Sprintf("%s; first %s — %s", headline, status, detail)
		}
	}
	return headline
}
