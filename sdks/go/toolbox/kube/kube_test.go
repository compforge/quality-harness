package kube

import (
	"context"
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"
)

const testNamespace = "quality"

func TestListPodsProjectsStableStateAndSorts(t *testing.T) {
	client := newTestClient(t,
		pod("worker-b", "uid-b", map[string]string{"app": "worker"}, corev1.PodRunning,
			condition(corev1.PodReady, corev1.ConditionTrue, "", "")),
		pod("worker-a", "uid-a", map[string]string{"app": "worker"}, corev1.PodPending,
			condition(corev1.PodScheduled, corev1.ConditionFalse, corev1.PodReasonUnschedulable, "insufficient cpu")),
		pod("database", "uid-db", map[string]string{"app": "mysql"}, corev1.PodRunning),
	)

	pods, err := client.ListPods(context.Background(), "app=worker")
	if err != nil {
		t.Fatal(err)
	}
	if len(pods) != 2 || pods[0].Name != "worker-a" || pods[1].Name != "worker-b" {
		t.Fatalf("ListPods() = %+v", pods)
	}
	if !pods[0].Unschedulable || pods[0].Reason != corev1.PodReasonUnschedulable || pods[0].Message != "insufficient cpu" {
		t.Fatalf("unschedulable Pod = %+v", pods[0])
	}
	if !pods[1].Ready {
		t.Fatalf("ready Pod = %+v", pods[1])
	}

	// Callers may annotate their observation without mutating client-go's cache.
	pods[0].Labels["test"] = "changed"
	readAgain, err := client.GetPod(context.Background(), "worker-a")
	if err != nil {
		t.Fatal(err)
	}
	if _, leaked := readAgain.Labels["test"]; leaked {
		t.Fatal("Pod labels alias the Kubernetes object")
	}
}

func TestDeletePodUsesUIDPrecondition(t *testing.T) {
	clientset := fake.NewSimpleClientset(pod("worker", "uid-worker", nil, corev1.PodRunning))
	client, err := New(clientset, testNamespace)
	if err != nil {
		t.Fatal(err)
	}

	ref := PodRef{Name: "worker", UID: "uid-worker"}
	if err := client.DeletePod(context.Background(), ref); err != nil {
		t.Fatal(err)
	}
	actions := clientset.Actions()
	deleteAction, ok := actions[len(actions)-1].(k8stesting.DeleteAction)
	if !ok {
		t.Fatalf("last action = %T, want DeleteAction", actions[len(actions)-1])
	}
	options := deleteAction.GetDeleteOptions()
	if options.Preconditions == nil || options.Preconditions.UID == nil || *options.Preconditions.UID != ref.UID {
		t.Fatalf("delete preconditions = %+v", options.Preconditions)
	}
	if options.GracePeriodSeconds != nil {
		t.Fatalf("delete grace period = %d, want Kubernetes default", *options.GracePeriodSeconds)
	}
	if _, err := clientset.CoreV1().Pods(testNamespace).Get(context.Background(), ref.Name, metav1.GetOptions{}); !apierrors.IsNotFound(err) {
		t.Fatalf("get deleted Pod error = %v, want NotFound", err)
	}
}

func TestForceDeletePodUsesZeroGracePeriodAndUIDPrecondition(t *testing.T) {
	clientset := fake.NewSimpleClientset(pod("worker", "uid-worker", nil, corev1.PodRunning))
	client, err := New(clientset, testNamespace)
	if err != nil {
		t.Fatal(err)
	}

	ref := PodRef{Name: "worker", UID: "uid-worker"}
	if err := client.ForceDeletePod(context.Background(), ref); err != nil {
		t.Fatal(err)
	}
	actions := clientset.Actions()
	deleteAction, ok := actions[len(actions)-1].(k8stesting.DeleteAction)
	if !ok {
		t.Fatalf("last action = %T, want DeleteAction", actions[len(actions)-1])
	}
	options := deleteAction.GetDeleteOptions()
	if options.Preconditions == nil || options.Preconditions.UID == nil || *options.Preconditions.UID != ref.UID {
		t.Fatalf("delete preconditions = %+v", options.Preconditions)
	}
	if options.GracePeriodSeconds == nil || *options.GracePeriodSeconds != 0 {
		t.Fatalf("delete grace period = %v, want 0", options.GracePeriodSeconds)
	}
}

func TestWaitReplacementThenReady(t *testing.T) {
	old := pod("worker-old", "uid-old", map[string]string{"app": "worker"}, corev1.PodRunning,
		condition(corev1.PodReady, corev1.ConditionTrue, "", ""))
	replacement := pod("worker-new", "uid-new", map[string]string{"app": "worker"}, corev1.PodRunning,
		condition(corev1.PodReady, corev1.ConditionTrue, "", ""))
	client := newTestClient(t, old, replacement)

	created, err := client.WaitReplacement(context.Background(), "app=worker", []Pod{podFrom(old)}, time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if created.UID != replacement.UID {
		t.Fatalf("replacement = %+v", created)
	}
	ready, err := client.WaitReady(context.Background(), created.Ref(), time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if !ready.Ready {
		t.Fatalf("ready Pod = %+v", ready)
	}
}

func TestWaitReadyRejectsReusedName(t *testing.T) {
	client := newTestClient(t, pod("worker", "uid-new", nil, corev1.PodRunning,
		condition(corev1.PodReady, corev1.ConditionTrue, "", "")))

	_, err := client.WaitReady(context.Background(), PodRef{Name: "worker", UID: "uid-old"}, time.Millisecond)
	if err == nil || !strings.Contains(err.Error(), "identity changed") {
		t.Fatalf("WaitReady() error = %v", err)
	}
}

func TestWaitUnschedulable(t *testing.T) {
	target := pod("worker", "uid-worker", nil, corev1.PodPending,
		condition(corev1.PodScheduled, corev1.ConditionFalse, corev1.PodReasonUnschedulable, "no matching nodes"))
	client := newTestClient(t, target)

	observed, err := client.WaitUnschedulable(context.Background(), podFrom(target).Ref(), time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if !observed.Unschedulable || observed.Message != "no matching nodes" {
		t.Fatalf("unschedulable Pod = %+v", observed)
	}
}

func TestListEventsScopesByPodUIDAndSorts(t *testing.T) {
	first := time.Date(2026, 8, 21, 10, 0, 0, 0, time.UTC)
	client := newTestClient(t,
		&corev1.Event{
			ObjectMeta:     metav1.ObjectMeta{Name: "late", Namespace: testNamespace},
			InvolvedObject: corev1.ObjectReference{Name: "worker", UID: "uid-worker"},
			Reason:         "Pulled", Message: "image pulled", Count: 1, LastTimestamp: metav1.NewTime(first.Add(time.Second)),
		},
		&corev1.Event{
			ObjectMeta:     metav1.ObjectMeta{Name: "early", Namespace: testNamespace},
			InvolvedObject: corev1.ObjectReference{Name: "worker", UID: "uid-worker"},
			Reason:         "Scheduled", Message: "assigned", Count: 1, LastTimestamp: metav1.NewTime(first),
		},
		&corev1.Event{
			ObjectMeta:     metav1.ObjectMeta{Name: "other", Namespace: testNamespace},
			InvolvedObject: corev1.ObjectReference{Name: "other", UID: "uid-other"}, Reason: "Ignored",
		},
	)

	events, err := client.ListEvents(context.Background(), PodRef{Name: "worker", UID: "uid-worker"})
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 2 || events[0].Reason != "Scheduled" || events[1].Reason != "Pulled" {
		t.Fatalf("ListEvents() = %+v", events)
	}
}

func TestNewValidatesNamespaceAndRequestLimits(t *testing.T) {
	if _, err := New(fake.NewSimpleClientset(), ""); err == nil {
		t.Fatal("New() accepted an empty namespace")
	}
	if _, err := NewForConfig(nil, Options{}); err == nil {
		t.Fatal("NewForConfig() accepted a nil REST config")
	}
}

func newTestClient(t *testing.T, objects ...runtime.Object) *Client {
	t.Helper()
	client, err := New(fake.NewSimpleClientset(objects...), testNamespace)
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func pod(name string, uid types.UID, labels map[string]string, phase corev1.PodPhase, conditions ...corev1.PodCondition) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: testNamespace, UID: uid, Labels: labels},
		Status:     corev1.PodStatus{Phase: phase, Conditions: conditions},
	}
}

func condition(conditionType corev1.PodConditionType, status corev1.ConditionStatus, reason, message string) corev1.PodCondition {
	return corev1.PodCondition{Type: conditionType, Status: status, Reason: reason, Message: message}
}
