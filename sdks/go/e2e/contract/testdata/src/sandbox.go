// This file is a discovery fixture, not compiled code (it lives under testdata/).
// It exercises the +case / +spec marker grammar that contract.Discover parses.

package src

import "net/http"

// StartSandbox creates a sandbox for a conversation.
//
// +spec=`tenant_id required; concurrent starts on the same (tenant, conversation) join the in-flight create and share one pod`
// +case:id=happy_minimal,desc=`minimal params create succeeds, returns sandbox_name`,input=`POST /api/v1/sandboxes/start with tenant_id, user_id, conversation_id`,expect=`200; sandbox_name non-empty; conversation_id echoed`,forbid=`creating more than one pod`
// +case:id=dup_conv,desc=`second start on same conversation reuses the same pod`,group=sandbox
// +case:id=missing_tenant,desc=`missing tenant_id is rejected`,expect=`HTTP 400`
func StartSandbox(w http.ResponseWriter, r *http.Request) {}

// StopSandbox stops a sandbox. Handlers without +case are ignored by discovery.
func StopSandbox(w http.ResponseWriter, r *http.Request) {}
