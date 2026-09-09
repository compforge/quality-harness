package kube

import (
	"context"
	"fmt"
	"sort"
	"strings"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/types"
)

// PodRef fences a mutating or waiting operation to one physical Pod instance.
// Name alone is insufficient because a controller can recreate a Pod with the
// same name while an E2E action is in flight.
type PodRef struct {
	Name string
	UID  types.UID
}

// Pod is the stable observation exposed to harness code. It intentionally
// omits the full Kubernetes object so cases do not couple themselves to
// client-go protocol details.
type Pod struct {
	Name          string
	UID           types.UID
	Labels        map[string]string
	Phase         corev1.PodPhase
	Ready         bool
	Deleting      bool
	Unschedulable bool
	Reason        string
	Message       string
}

func (p Pod) Ref() PodRef {
	return PodRef{Name: p.Name, UID: p.UID}
}

// ListPods returns a deterministic snapshot of Pods matching a Kubernetes
// label selector in the Client's namespace.
func (c *Client) ListPods(ctx context.Context, selector string) ([]Pod, error) {
	if _, err := labels.Parse(selector); err != nil {
		return nil, fmt.Errorf("list Pods in namespace %q: parse label selector %q: %w", c.namespace, selector, err)
	}
	items, err := c.client.CoreV1().Pods(c.namespace).List(ctx, metav1.ListOptions{LabelSelector: selector})
	if err != nil {
		return nil, fmt.Errorf("list Pods in namespace %q with selector %q: %w", c.namespace, selector, err)
	}
	pods := make([]Pod, 0, len(items.Items))
	for i := range items.Items {
		pods = append(pods, podFrom(&items.Items[i]))
	}
	sort.Slice(pods, func(i, j int) bool { return pods[i].Name < pods[j].Name })
	return pods, nil
}

// GetPod reads one Pod and returns its current stable observation.
func (c *Client) GetPod(ctx context.Context, name string) (Pod, error) {
	if strings.TrimSpace(name) == "" {
		return Pod{}, fmt.Errorf("get Pod in namespace %q: name is required", c.namespace)
	}
	pod, err := c.client.CoreV1().Pods(c.namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return Pod{}, fmt.Errorf("get Pod %q in namespace %q: %w", name, c.namespace, err)
	}
	return podFrom(pod), nil
}

// DeletePod deletes exactly the physical Pod identified by ref. The UID
// precondition prevents a delayed fault action from deleting a replacement Pod
// that reused the same name.
func (c *Client) DeletePod(ctx context.Context, ref PodRef) error {
	return c.deletePod(ctx, ref, nil)
}

// ForceDeletePod deletes exactly the physical Pod without allowing its normal
// termination grace period. It is intended for explicit crash-recovery cases;
// callers still own disruption authorization, target selection, and cleanup.
func (c *Client) ForceDeletePod(ctx context.Context, ref PodRef) error {
	gracePeriod := int64(0)
	return c.deletePod(ctx, ref, &gracePeriod)
}

func (c *Client) deletePod(ctx context.Context, ref PodRef, gracePeriod *int64) error {
	if err := validatePodRef(ref); err != nil {
		return fmt.Errorf("delete Pod in namespace %q: %w", c.namespace, err)
	}
	propagation := metav1.DeletePropagationBackground
	if err := c.client.CoreV1().Pods(c.namespace).Delete(ctx, ref.Name, metav1.DeleteOptions{
		Preconditions:      &metav1.Preconditions{UID: &ref.UID},
		PropagationPolicy:  &propagation,
		GracePeriodSeconds: gracePeriod,
	}); err != nil {
		return fmt.Errorf("delete Pod %q uid %q in namespace %q: %w", ref.Name, ref.UID, c.namespace, err)
	}
	return nil
}

func validatePodRef(ref PodRef) error {
	if strings.TrimSpace(ref.Name) == "" {
		return fmt.Errorf("Pod name is required")
	}
	if ref.UID == "" {
		return fmt.Errorf("Pod UID is required")
	}
	return nil
}

func podFrom(pod *corev1.Pod) Pod {
	result := Pod{
		Name:     pod.Name,
		UID:      pod.UID,
		Labels:   cloneLabels(pod.Labels),
		Phase:    pod.Status.Phase,
		Deleting: pod.DeletionTimestamp != nil,
	}
	for _, condition := range pod.Status.Conditions {
		switch condition.Type {
		case corev1.PodReady:
			result.Ready = condition.Status == corev1.ConditionTrue && pod.DeletionTimestamp == nil
		case corev1.PodScheduled:
			if condition.Status == corev1.ConditionFalse && condition.Reason == corev1.PodReasonUnschedulable {
				result.Unschedulable = true
				result.Reason = condition.Reason
				result.Message = condition.Message
			}
		}
	}
	return result
}

func cloneLabels(source map[string]string) map[string]string {
	if source == nil {
		return nil
	}
	cloned := make(map[string]string, len(source))
	for key, value := range source {
		cloned[key] = value
	}
	return cloned
}
