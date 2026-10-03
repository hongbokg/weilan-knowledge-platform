package service

import (
	"github.com/Tencent/WeKnora/internal/types"
	"testing"
)

func TestSummaryForcedReasoningBudget(t *testing.T) {
	opts := summaryChatOptions("GLM-5.3-Flash", 1024)
	level, _ := opts.Reasoning()
	if opts.MaxTokens != 4096 || level != "low" || opts.Thinking != nil {
		t.Fatal("forced thinking must have a usable bounded budget and low effort")
	}
	if summaryChatOptions("glm-5.3-flash", 100000).MaxTokens != 8192 {
		t.Fatal("budget must be capped")
	}
	normal := summaryChatOptions("other-model", 1024)
	level, _ = normal.Reasoning()
	if normal.MaxTokens != 1024 || level != "off" {
		t.Fatal("unrelated models must keep configured behavior")
	}
}

func TestSummaryBudgetRetrySafety(t *testing.T) {
	opts := summaryChatOptions("glm-5.3-flash", 1024)
	truncated := &types.ChatResponse{FinishReason: "length", ReasoningContent: "private reasoning"}
	if !summaryNeedsLargerBudget(truncated, opts) {
		t.Fatal("reasoning-only truncation needs a bounded retry")
	}
	truncated.Content = "valid summary"
	if summaryNeedsLargerBudget(truncated, opts) {
		t.Fatal("do not replay usable output")
	}
	truncated.Content = ""
	truncated.FinishReason = "stop"
	if summaryNeedsLargerBudget(truncated, opts) {
		t.Fatal("ordinary empty output is not a truncation")
	}
	truncated.FinishReason = "length"
	opts.MaxTokens = 8192
	if summaryNeedsLargerBudget(truncated, opts) {
		t.Fatal("no unbounded escalation")
	}
	if summaryNeedsLargerBudget(nil, opts) {
		t.Fatal("nil is not proof of truncation")
	}
}
