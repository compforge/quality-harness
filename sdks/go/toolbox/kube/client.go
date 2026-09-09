package kube

import (
	"fmt"
	"strings"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

// Options scopes every operation to one namespace and configures the capacity
// and timeout of Kubernetes API requests made by clients constructed here.
type Options struct {
	Namespace      string
	RequestTimeout time.Duration
	QPS            float32
	Burst          int
}

// Client exposes Kubernetes operations useful to multiple quality-harness views.
// It is namespace-scoped so a selector cannot accidentally expand to the whole
// cluster.
type Client struct {
	client    kubernetes.Interface
	namespace string
}

// FromKubeconfig constructs a Client from an explicit kubeconfig path. An
// optional context overrides the kubeconfig's current context.
func FromKubeconfig(path, contextName string, options Options) (*Client, error) {
	if strings.TrimSpace(path) == "" {
		return nil, fmt.Errorf("open Kubernetes client: kubeconfig path is required")
	}
	loadingRules := &clientcmd.ClientConfigLoadingRules{ExplicitPath: path}
	overrides := &clientcmd.ConfigOverrides{CurrentContext: contextName}
	restConfig, err := clientcmd.NewNonInteractiveDeferredLoadingClientConfig(loadingRules, overrides).ClientConfig()
	if err != nil {
		return nil, fmt.Errorf("load kubeconfig %q: %w", path, err)
	}
	return NewForConfig(restConfig, options)
}

// InCluster constructs a Client from the service account mounted in a Pod.
func InCluster(options Options) (*Client, error) {
	restConfig, err := rest.InClusterConfig()
	if err != nil {
		return nil, fmt.Errorf("load in-cluster Kubernetes config: %w", err)
	}
	return NewForConfig(restConfig, options)
}

// NewForConfig constructs a Client from a caller-provided REST config.
func NewForConfig(restConfig *rest.Config, options Options) (*Client, error) {
	if restConfig == nil {
		return nil, fmt.Errorf("open Kubernetes client: REST config is required")
	}
	if err := validateOptions(options); err != nil {
		return nil, err
	}
	configured := rest.CopyConfig(restConfig)
	configured.Timeout = options.RequestTimeout
	configured.QPS = options.QPS
	configured.Burst = options.Burst
	client, err := kubernetes.NewForConfig(configured)
	if err != nil {
		return nil, fmt.Errorf("create Kubernetes client: %w", err)
	}
	return New(client, options.Namespace)
}

// New wraps an existing Kubernetes client. The caller owns its transport
// capacity and timeout configuration; this constructor is also useful with the
// client-go fake clientset.
func New(client kubernetes.Interface, namespace string) (*Client, error) {
	if client == nil {
		return nil, fmt.Errorf("open Kubernetes client: client is required")
	}
	if strings.TrimSpace(namespace) == "" {
		return nil, fmt.Errorf("open Kubernetes client: namespace is required")
	}
	return &Client{client: client, namespace: namespace}, nil
}

func validateOptions(options Options) error {
	if strings.TrimSpace(options.Namespace) == "" {
		return fmt.Errorf("open Kubernetes client: namespace is required")
	}
	if options.RequestTimeout <= 0 {
		return fmt.Errorf("open Kubernetes client: request timeout must be positive")
	}
	if options.QPS <= 0 {
		return fmt.Errorf("open Kubernetes client: QPS must be positive")
	}
	if options.Burst <= 0 {
		return fmt.Errorf("open Kubernetes client: burst must be positive")
	}
	return nil
}
