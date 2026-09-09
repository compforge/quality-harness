package core

import (
	"encoding/json"
	"io"
	"net/http"
	"time"
)

// healthzClient probes capabilities with a short timeout, deliberately separate
// from the test HTTP client: a slow or hung server must not stall suite setup.
var healthzClient = &http.Client{Timeout: 5 * time.Second}

// ProbeCapabilities hits <base_url><path> (e.g. "/healthz") once and records the
// server's self-reported capabilities into config.Capabilities. Call it after LoadConfig
// when tests need to gate on server features they can't know statically.
func (e *E2EConfig) ProbeCapabilities(path string) {
	e.Capabilities = FetchCapabilities(e.Service.BaseURL, path)
}

// FetchCapabilities GETs a capability endpoint and parses its JSON body. It degrades
// gracefully: on any transport error or non-JSON body it returns Known=false, so
// RequireCapability skips (not fails) — older servers that answer plain "ok" on
// /healthz are tolerated rather than breaking the suite.
func FetchCapabilities(baseURL, path string) Capabilities {
	resp, err := healthzClient.Get(baseURL + path)
	if err != nil {
		return Capabilities{Known: false}
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil || !json.Valid(raw) {
		return Capabilities{Known: false}
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return Capabilities{Known: false}
	}
	return Capabilities{Known: true, Data: data}
}

// Has reports whether the server announced a capability key with a non-nil value.
func (c Capabilities) Has(key string) bool {
	if !c.Known {
		return false
	}
	v, ok := c.Data[key]
	return ok && v != nil
}

// String returns a capability value as a string, or "" if absent/unknown.
func (c Capabilities) String(key string) string {
	if !c.Known {
		return ""
	}
	if v, ok := c.Data[key]; ok && v != nil {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}
