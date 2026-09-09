// Package report is the Go home for the neutral report IR + rendering shared by
// eval and perf, the counterpart of python/report_kit. It carries no business
// concepts. Placeholder for now: the e2e SDK leaves result reporting to `go test`;
// report fills in when eval/perf land. It is the one package the three test SDKs
// are allowed to share.
package report
