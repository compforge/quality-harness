package kube

import (
	"context"
	"fmt"
	"sort"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
)

// Event is the evidence-bearing subset of a Kubernetes Event useful to a test
// report or failure message.
type Event struct {
	Type       string
	Reason     string
	Message    string
	Count      int32
	ObservedAt time.Time
}

// ListEvents returns Events attached to the exact physical Pod instance.
func (c *Client) ListEvents(ctx context.Context, ref PodRef) ([]Event, error) {
	if err := validatePodRef(ref); err != nil {
		return nil, fmt.Errorf("list Pod Events in namespace %q: %w", c.namespace, err)
	}
	selector := fields.OneTermEqualSelector("involvedObject.uid", string(ref.UID)).String()
	items, err := c.client.CoreV1().Events(c.namespace).List(ctx, metav1.ListOptions{FieldSelector: selector})
	if err != nil {
		return nil, fmt.Errorf("list Events for Pod %q uid %q in namespace %q: %w", ref.Name, ref.UID, c.namespace, err)
	}
	events := make([]Event, 0, len(items.Items))
	for i := range items.Items {
		event := &items.Items[i]
		if event.InvolvedObject.UID != ref.UID {
			continue
		}
		events = append(events, Event{
			Type: event.Type, Reason: event.Reason, Message: event.Message,
			Count: event.Count, ObservedAt: eventObservedAt(event),
		})
	}
	sort.SliceStable(events, func(i, j int) bool {
		if events[i].ObservedAt.Equal(events[j].ObservedAt) {
			return events[i].Reason < events[j].Reason
		}
		return events[i].ObservedAt.Before(events[j].ObservedAt)
	})
	return events, nil
}

func eventObservedAt(event *corev1.Event) time.Time {
	if !event.EventTime.IsZero() {
		return event.EventTime.Time
	}
	if event.Series != nil && !event.Series.LastObservedTime.IsZero() {
		return event.Series.LastObservedTime.Time
	}
	if !event.LastTimestamp.IsZero() {
		return event.LastTimestamp.Time
	}
	if !event.FirstTimestamp.IsZero() {
		return event.FirstTimestamp.Time
	}
	return event.CreationTimestamp.Time
}
