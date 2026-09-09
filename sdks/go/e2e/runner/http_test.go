package runner

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/compforge/quality-harness/sdks/go/e2e/core"
)

func TestJSONRunnerUsesGenericHeadersAndExplicitExclusions(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if got := req.Header.Get("Authorization"); got != "" {
			t.Errorf("excluded Authorization = %q", got)
		}
		if got := req.Header.Get("X-Request"); got != "value" {
			t.Errorf("X-Request = %q", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()

	runner := NewJSONRunner(&core.E2EConfig{
		Service: core.Service{
			BaseURL: server.URL,
			Headers: map[string]string{"Authorization": "Bearer token"},
		},
		Runtime: core.RuntimeConfig{HTTPTimeoutS: 1},
	})
	outcome, err := runner.Trigger(context.Background(), Request{
		Path:           "/run",
		Headers:        map[string]string{"X-Request": "value"},
		ExcludeHeaders: []string{"Authorization"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if outcome.StatusCode != http.StatusOK || outcome.Field("ok") != true {
		t.Fatalf("outcome = %+v", outcome)
	}
}
