// Package judge provides assertion helpers for e2e test outcomes.
package judge

import (
	"fmt"
	"regexp"

	"github.com/compforge/quality-harness/sdks/go/e2e/runner"
)

// Assertion is a check function applied to an Outcome.
type Assertion func(o *runner.Outcome) error

// Assert runs all assertions against the outcome and returns the first mismatch.
func Assert(o *runner.Outcome, asserts ...Assertion) error {
	for _, a := range asserts {
		if err := a(o); err != nil {
			return err
		}
	}
	return nil
}

// Status asserts HTTP status code equals want.
func Status(want int) Assertion {
	return func(o *runner.Outcome) error {
		if o.StatusCode != want {
			return fmt.Errorf("expected status %d, got %d", want, o.StatusCode)
		}
		return nil
	}
}

// Status2xx asserts HTTP status code is in 2xx range.
func Status2xx() Assertion {
	return func(o *runner.Outcome) error {
		if o.StatusCode < 200 || o.StatusCode >= 300 {
			return fmt.Errorf("expected 2xx status, got %d", o.StatusCode)
		}
		return nil
	}
}

// FieldEq asserts body field at dot-path equals want.
func FieldEq(path string, want any) Assertion {
	return func(o *runner.Outcome) error {
		got := o.Field(path)
		if fmt.Sprint(got) != fmt.Sprint(want) {
			return fmt.Errorf("field %q: expected %v, got %v", path, want, got)
		}
		return nil
	}
}

// FieldNotEmpty asserts body field at dot-path is present and non-empty.
func FieldNotEmpty(path string) Assertion {
	return func(o *runner.Outcome) error {
		got := o.Field(path)
		if got == nil || fmt.Sprint(got) == "" {
			return fmt.Errorf("field %q: expected non-empty value, got %v", path, got)
		}
		return nil
	}
}

// FieldContains asserts body field string contains substring.
func FieldContains(path string, substr string) Assertion {
	return func(o *runner.Outcome) error {
		got := o.FieldStr(path)
		if got == "" {
			return fmt.Errorf("field %q: expected to contain %q, got empty", path, substr)
		}
		if !contains(got, substr) {
			return fmt.Errorf("field %q: expected to contain %q, got %q", path, substr, got)
		}
		return nil
	}
}

// FieldMatches asserts body field string matches regex pattern.
func FieldMatches(path string, pattern string) Assertion {
	re := regexp.MustCompile(pattern)
	return func(o *runner.Outcome) error {
		got := o.FieldStr(path)
		if !re.MatchString(got) {
			return fmt.Errorf("field %q: expected to match %q, got %q", path, pattern, got)
		}
		return nil
	}
}

// FieldIn asserts body field value is one of the allowed values.
func FieldIn(path string, values ...any) Assertion {
	return func(o *runner.Outcome) error {
		got := o.Field(path)
		gotStr := fmt.Sprint(got)
		for _, v := range values {
			if gotStr == fmt.Sprint(v) {
				return nil
			}
		}
		return fmt.Errorf("field %q: expected one of %v, got %v", path, values, got)
	}
}

// NoError asserts response has no error/code fields.
func NoError() Assertion {
	return func(o *runner.Outcome) error {
		m := o.BodyMap()
		if m == nil {
			return nil
		}
		if errMsg, _ := m["error"].(string); errMsg != "" {
			code, _ := m["code"].(string)
			return fmt.Errorf("unexpected error: error=%q code=%q", errMsg, code)
		}
		return nil
	}
}

func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(substr) == 0 ||
		findSubstring(s, substr))
}

func findSubstring(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
