package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/compforge/quality-harness/sdks/go/e2e/core"
)

// JSONRunner sends HTTP requests and parses JSON responses into Outcome.
type JSONRunner struct {
	config *core.E2EConfig
	client *http.Client
}

// NewJSONRunner creates a runner for standard JSON REST/RPC APIs.
func NewJSONRunner(config *core.E2EConfig) *JSONRunner {
	timeout := time.Duration(config.Runtime.HTTPTimeoutS) * time.Second
	if timeout <= 0 {
		timeout = 120 * time.Second
	}
	return &JSONRunner{
		config: config,
		client: &http.Client{
			Timeout: timeout,
		},
	}
}

func (r *JSONRunner) Trigger(ctx context.Context, req Request) (*Outcome, error) {
	var bodyReader io.Reader
	if req.Body != nil {
		data, err := json.Marshal(req.Body)
		if err != nil {
			return nil, fmt.Errorf("marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	method := req.Method
	if method == "" {
		method = http.MethodPost
	}

	url := r.config.Service.BaseURL + req.Path
	httpReq, err := http.NewRequestWithContext(ctx, method, url, bodyReader)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}

	// Set headers
	httpReq.Header.Set("Content-Type", "application/json")
	for k, v := range r.config.Service.Headers {
		httpReq.Header.Set(k, v)
	}
	for k, v := range req.Headers {
		httpReq.Header.Set(k, v)
	}
	for _, key := range req.ExcludeHeaders {
		httpReq.Header.Del(key)
	}

	// Set query params
	if len(req.Query) > 0 {
		q := httpReq.URL.Query()
		for k, v := range req.Query {
			q.Set(k, v)
		}
		httpReq.URL.RawQuery = q.Encode()
	}

	start := time.Now()
	resp, err := r.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()
	durationMS := time.Since(start).Milliseconds()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response body: %w", err)
	}

	// Parse response headers
	headers := make(map[string]string, len(resp.Header))
	for k := range resp.Header {
		headers[k] = resp.Header.Get(k)
	}

	// Parse JSON body if content-type is JSON
	var body json.RawMessage
	ct := resp.Header.Get("Content-Type")
	if len(raw) > 0 && (ct == "" || bytes.Contains([]byte(ct), []byte("json"))) {
		if json.Valid(raw) {
			body = raw
		}
	}

	return &Outcome{
		StatusCode: resp.StatusCode,
		Body:       body,
		Headers:    headers,
		DurationMS: durationMS,
		Metadata:   map[string]any{},
		Raw:        raw,
	}, nil
}
