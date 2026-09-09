package runner

import "testing"

func TestOutcomeDecode(t *testing.T) {
	o := &Outcome{Body: []byte(`{"sandbox_name":"sb-1","exit_code":0}`)}
	var got struct {
		SandboxName string `json:"sandbox_name"`
		ExitCode    int    `json:"exit_code"`
	}
	if err := o.Decode(&got); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if got.SandboxName != "sb-1" || got.ExitCode != 0 {
		t.Errorf("decoded wrong: %+v", got)
	}

	if err := (&Outcome{Body: []byte(`not json`)}).Decode(&got); err == nil {
		t.Error("expected error decoding invalid JSON")
	}
}
