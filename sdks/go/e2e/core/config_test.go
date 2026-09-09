package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInterpolate(t *testing.T) {
	os.Setenv("TEST_VAR", "hello")
	defer os.Unsetenv("TEST_VAR")

	got, err := interpolate("${TEST_VAR}")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "hello" {
		t.Fatalf("expected %q, got %q", "hello", got)
	}
}

func TestInterpolateDefault(t *testing.T) {
	os.Unsetenv("MISSING_VAR")

	got, err := interpolate("${MISSING_VAR:-fallback}")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != "fallback" {
		t.Fatalf("expected %q, got %q", "fallback", got)
	}
}

func TestInterpolateMissingRequired(t *testing.T) {
	os.Unsetenv("REQUIRED_VAR")

	_, err := interpolate("${REQUIRED_VAR}")
	if err == nil {
		t.Fatal("expected error for missing required var")
	}
}

func TestLoadConfigFromYAML(t *testing.T) {
	os.Setenv("MY_URL", "http://localhost:9090")
	os.Setenv("MY_TOKEN", "token-1")
	defer func() {
		os.Unsetenv("MY_URL")
		os.Unsetenv("MY_TOKEN")
	}()

	dir := t.TempDir()
	configPath := filepath.Join(dir, "config.yaml")
	content := `
service:
  name: test-svc
  component:
    repository: {forge: github, path: example/service}
    name: api
  environment:
    name: dev
    kubeconfig: /tmp/kubeconfig
    context: dev-cluster
  base_url: ${MY_URL}
  headers:
    Authorization: Bearer ${MY_TOKEN}
profile: minimal
custom:
  ttl: "60"
`
	if err := os.WriteFile(configPath, []byte(content), 0644); err != nil {
		t.Fatal(err)
	}

	config, err := LoadConfig(configPath)
	if err != nil {
		t.Fatalf("LoadConfig error: %v", err)
	}
	if config.Service.Name != "test-svc" {
		t.Errorf("service.name = %q, want %q", config.Service.Name, "test-svc")
	}
	if config.Service.Component.Repository.Forge.Name != "github" || config.Service.Component.Repository.Path != "example/service" {
		t.Errorf("service component repository = %#v", config.Service.Component.Repository)
	}
	if config.Service.Environment.Name != "dev" || config.Service.Environment.Kubeconfig != "/tmp/kubeconfig" || config.Service.Environment.Context != "dev-cluster" {
		t.Errorf("service environment = %#v", config.Service.Environment)
	}
	if config.Service.BaseURL != "http://localhost:9090" {
		t.Errorf("service.base_url = %q, want %q", config.Service.BaseURL, "http://localhost:9090")
	}
	if config.Service.Headers["Authorization"] != "Bearer token-1" {
		t.Errorf("service headers = %#v", config.Service.Headers)
	}
	if config.Profile != "minimal" {
		t.Errorf("profile = %q, want %q", config.Profile, "minimal")
	}
	if config.Custom["ttl"] != "60" {
		t.Errorf("custom.ttl = %q, want %q", config.Custom["ttl"], "60")
	}
}

func TestLoadConfigFallback(t *testing.T) {
	os.Setenv("E2E_BASE_URL", "http://fallback:8080")
	os.Setenv("E2E_SERVICE_HEADERS", `{"Authorization":"Bearer fallback"}`)
	os.Setenv("E2E_PROFILE", "full")
	defer func() {
		os.Unsetenv("E2E_BASE_URL")
		os.Unsetenv("E2E_SERVICE_HEADERS")
		os.Unsetenv("E2E_PROFILE")
	}()

	config, err := LoadConfig("/nonexistent/config.yaml")
	if err != nil {
		t.Fatalf("LoadConfig error: %v", err)
	}
	if config.Service.BaseURL != "http://fallback:8080" {
		t.Errorf("base_url = %q, want %q", config.Service.BaseURL, "http://fallback:8080")
	}
	if config.Service.Headers["Authorization"] != "Bearer fallback" {
		t.Errorf("service headers = %#v", config.Service.Headers)
	}
	if config.Profile != "full" {
		t.Errorf("profile = %q, want %q", config.Profile, "full")
	}
}

func TestLoadConfigRejectsInvalidServiceHeadersJSON(t *testing.T) {
	os.Setenv("E2E_SERVICE_HEADERS", "not-json")
	defer os.Unsetenv("E2E_SERVICE_HEADERS")
	if _, err := LoadConfig("/nonexistent/config.yaml"); err == nil {
		t.Fatal("expected invalid E2E_SERVICE_HEADERS error")
	}
}

func TestLoadConfigRejectsNonMappingServiceHeaders(t *testing.T) {
	configPath := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(configPath, []byte("service:\n  headers: bearer-token\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadConfig(configPath); err == nil {
		t.Fatal("expected non-mapping service.headers error")
	}
}

func TestLoadConfigRejectsUnknownFields(t *testing.T) {
	tests := []struct {
		name    string
		content string
		field   string
	}{
		{name: "top-level", content: "auth:\n  headers: {}\n", field: "auth"},
		{name: "service", content: "service:\n  base_url: http://localhost\n  typo: true\n", field: "typo"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			configPath := filepath.Join(t.TempDir(), "config.yaml")
			if err := os.WriteFile(configPath, []byte(tt.content), 0o644); err != nil {
				t.Fatal(err)
			}
			_, err := LoadConfig(configPath)
			if err == nil || !strings.Contains(err.Error(), tt.field) {
				t.Fatalf("expected unknown field %q error, got %v", tt.field, err)
			}
		})
	}
}
