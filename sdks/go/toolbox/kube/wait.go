package kube

import (
	"context"
	"fmt"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// WaitReplacement waits for a Pod matching selector whose UID was absent from
// the previous snapshot. It reports creation, not readiness; call WaitReady
// when the case requires a usable replacement.
func (c *Client) WaitReplacement(
	ctx context.Context,
	selector string,
	previous []Pod,
	interval time.Duration,
) (Pod, error) {
	if len(previous) == 0 {
		return Pod{}, fmt.Errorf("wait for replacement Pod: previous snapshot is empty")
	}
	known := make(map[types.UID]struct{}, len(previous))
	for _, pod := range previous {
		if pod.UID == "" {
			return Pod{}, fmt.Errorf("wait for replacement Pod: previous Pod %q has no UID", pod.Name)
		}
		known[pod.UID] = struct{}{}
	}
	var replacement Pod
	err := poll(ctx, interval, func(ctx context.Context) (bool, error) {
		pods, err := c.ListPods(ctx, selector)
		if err != nil {
			return false, err
		}
		for _, pod := range pods {
			if _, exists := known[pod.UID]; !exists {
				replacement = pod
				return true, nil
			}
		}
		return false, nil
	})
	if err != nil {
		return Pod{}, fmt.Errorf("wait for replacement Pod in namespace %q with selector %q: %w", c.namespace, selector, err)
	}
	return replacement, nil
}

// WaitReady waits until the exact Pod instance is Ready and not terminating.
func (c *Client) WaitReady(ctx context.Context, ref PodRef, interval time.Duration) (Pod, error) {
	return c.waitPod(ctx, ref, interval, "Ready", func(pod Pod) bool { return pod.Ready && !pod.Deleting })
}

// WaitUnschedulable waits until the exact Pod instance has a PodScheduled=False
// condition whose reason is Unschedulable.
func (c *Client) WaitUnschedulable(ctx context.Context, ref PodRef, interval time.Duration) (Pod, error) {
	return c.waitPod(ctx, ref, interval, "Unschedulable", func(pod Pod) bool { return pod.Unschedulable })
}

func (c *Client) waitPod(
	ctx context.Context,
	ref PodRef,
	interval time.Duration,
	condition string,
	matches func(Pod) bool,
) (Pod, error) {
	if err := validatePodRef(ref); err != nil {
		return Pod{}, fmt.Errorf("wait for Pod condition %s: %w", condition, err)
	}
	var observed Pod
	err := poll(ctx, interval, func(ctx context.Context) (bool, error) {
		pod, err := c.client.CoreV1().Pods(c.namespace).Get(ctx, ref.Name, metav1.GetOptions{})
		if apierrors.IsNotFound(err) {
			return false, nil
		}
		if err != nil {
			return false, fmt.Errorf("get Pod %q in namespace %q: %w", ref.Name, c.namespace, err)
		}
		observed = podFrom(pod)
		if observed.UID != ref.UID {
			return false, fmt.Errorf("Pod %q identity changed from uid %q to %q", ref.Name, ref.UID, observed.UID)
		}
		return matches(observed), nil
	})
	if err != nil {
		return Pod{}, fmt.Errorf("wait for Pod %q uid %q in namespace %q to become %s: %w", ref.Name, ref.UID, c.namespace, condition, err)
	}
	return observed, nil
}

func poll(ctx context.Context, interval time.Duration, check func(context.Context) (bool, error)) error {
	if interval <= 0 {
		return fmt.Errorf("poll interval must be positive")
	}
	for {
		done, err := check(ctx)
		if err != nil {
			return err
		}
		if done {
			return nil
		}
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}
