//go:build linux

package chat

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/Tencent/WeKnora/internal/types"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
)

func setupCost(t *testing.T, limit int64) {
	t.Helper()
	d := t.TempDir()
	t.Setenv("WEKNORA_LLM_COST_DIR", d)
	os.Mkdir(filepath.Join(d, "cache"), 0700)
	if e := costAtomic(filepath.Join(d, "policy.json"), costPolicy{Daily:limit, Monthly:limit * 30, MaxConcurrent:2}, 0600); e != nil {
		t.Fatal(e)
	}
}
func TestCostAdmissionConcurrencyAndDurability(t *testing.T) {
	setupCost(t, 100)
	var wg sync.WaitGroup
	var admitted atomic.Int32
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, e := costBegin("model", "wiki", 60); e == nil {
				admitted.Add(1)
			}
		}()
	}
	wg.Wait()
	if admitted.Load() != 1 {
		t.Fatal(admitted.Load())
	}
	b, _ := os.ReadFile(filepath.Join(costDir(), "usage.json"))
	var s costState
	if e := json.Unmarshal(b, &s); e != nil {
		t.Fatal(e)
	}
	d, m := costTotals(&s, costDay())
	if d != 60 || m != 60 {
		t.Fatal(d, m)
	}
}
func TestCostActualUsageAndCooldown(t *testing.T) {
	setupCost(t, 1000)
	id, e := costBegin("model", "summary", 500)
	if e != nil {
		t.Fatal(e)
	}
	if e = costFinish(id, &types.TokenUsage{PromptTokens: 10, CompletionTokens: 20, TotalTokens: 30}, nil); e != nil {
		t.Fatal(e)
	}
	id, e = costBegin("model", "summary", 500)
	if e != nil {
		t.Fatal(e)
	}
	costFinish(id, nil, fmt.Errorf("HTTP 429"))
	if _, e = costBegin("model", "summary", 1); e == nil {
		t.Fatal("cooldown bypass")
	}
	costTxn(func(s *costState, p costPolicy) error {
		d, _ := costTotals(s, costDay())
		if d != 530 {
			t.Fatal(d)
		}
		return nil
	})
}

type costFake struct{ n int }

func (f *costFake) GetModelID() string   { return "fake" }
func (f *costFake) GetModelName() string { return "fake" }
func (f *costFake) Chat(context.Context, []Message, *ChatOptions) (*types.ChatResponse, error) {
	f.n++
	return &types.ChatResponse{Content: "summary", Usage: types.TokenUsage{TotalTokens: 10}}, nil
}
func (f *costFake) ChatStream(context.Context, []Message, *ChatOptions) (<-chan types.StreamResponse, error) {
	c := make(chan types.StreamResponse, 1)
	c <- types.StreamResponse{Done: true, Usage: &types.TokenUsage{TotalTokens: 20}}
	close(c)
	return c, nil
}
func TestCostSummaryCacheContentAndTenantIsolation(t *testing.T) {
	setupCost(t, 100000)
	f := &costFake{}
	w := &costChat{f}
	ctx := context.WithValue(context.Background(), types.TenantIDContextKey, uint64(1))
	ctx = types.WithLLMCallMetadata(ctx, "document_summary", "")
	m := []Message{{Role: "user", Content: "one"}}
	for i := 0; i < 2; i++ {
		if _, e := w.Chat(ctx, m, nil); e != nil {
			t.Fatal(e)
		}
	}
	if f.n != 1 {
		t.Fatal(f.n)
	}
	m[0].Content = "two"
	w.Chat(ctx, m, nil)
	ctx = context.WithValue(ctx, types.TenantIDContextKey, uint64(2))
	w.Chat(ctx, m, nil)
	if f.n != 3 {
		t.Fatal(f.n)
	}
}
func TestCostStreamingSettlementAndMonthlyBudget(t *testing.T) {
	setupCost(t, 100000)
	w := &costChat{&costFake{}}
	ch, e := w.ChatStream(context.Background(), nil, nil)
	if e != nil {
		t.Fatal(e)
	}
	for range ch {
	}
	costTxn(func(s *costState, p costPolicy) error {
		if len(s.Pending) != 0 {
			t.Fatal("unsettled stream")
		}
		d, _ := costTotals(s, costDay())
		if d != 20 {
			t.Fatal(d)
		}
		return nil
	})
}
func TestCostMonthlyCapAcrossDays(t *testing.T) {
	setupCost(t, 1000)
	if e := costAtomic(filepath.Join(costDir(), "policy.json"), costPolicy{Daily:1000, Monthly:100, MaxConcurrent:2}, 0600); e != nil {
		t.Fatal(e)
	}
	costTxn(func(s *costState, p costPolicy) error {
		s.Stats[costDay()[:7]+"-00|wiki|m"] = &costStat{Actual: 90}
		return nil
	})
	if _, e := costBegin("m", "summary", 20); e == nil {
		t.Fatal("monthly cap bypassed")
	}
}
func TestCostCorruptLedgerFailsClosed(t *testing.T) {
	setupCost(t, 1000)
	os.WriteFile(filepath.Join(costDir(), "usage.json"), []byte("broken"), 0600)
	if _, e := costBegin("m", "summary", 1); e == nil {
		t.Fatal("corruption bypassed")
	}
}
