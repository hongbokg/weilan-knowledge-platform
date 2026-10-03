//go:build linux

package chat

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/Tencent/WeKnora/internal/types"
)

// Local durable budget for this single-host deployment. Fail closed on ledger errors.
// No prompts, API keys, paths, or generated content are stored in the usage ledger.
type costPolicy struct {
	UnlimitedTokenModels []string `json:"unlimited_token_models"`
	Daily                int64    `json:"daily_tokens"`
	Monthly              int64    `json:"monthly_tokens"`
	MaxConcurrent        int      `json:"max_concurrent"`
}
type costStat struct {
	Calls     int64 `json:"calls"`
	Input     int64 `json:"input_tokens"`
	Output    int64 `json:"output_tokens"`
	Actual    int64 `json:"actual_tokens"`
	Estimated int64 `json:"estimated_tokens"`
	CacheHits int64 `json:"cache_hits"`
	Errors    int64 `json:"errors"`
}
type costReservation struct {
	Day     string `json:"day"`
	Task    string `json:"task"`
	Model   string `json:"model"`
	Tokens  int64  `json:"tokens"`
	Started int64  `json:"started"`
}
type costState struct {
	Stats    map[string]*costStat       `json:"stats"`
	Pending  map[string]costReservation `json:"pending"`
	Cooldown map[string]int64           `json:"cooldown"`
}

var costMutex sync.Mutex
var costZone = time.FixedZone("Asia/Shanghai", 8*3600)

func costDay() string { return time.Now().In(costZone).Format("2006-01-02") }
func costDir() string { return os.Getenv("WEKNORA_LLM_COST_DIR") }
func costAtomic(path string, v any, mode os.FileMode) error {
	b, e := json.Marshal(v)
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".cost-")
	if e != nil {
		return e
	}
	n := f.Name()
	defer os.Remove(n)
	if _, e = f.Write(b); e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	if e = os.Chmod(n, mode); e != nil {
		return e
	}
	return os.Rename(n, path)
}

// Reservations belong to native calls on this single host. Recover after
// a process exits, but retain an estimate rather than inventing zero usage.
func reservationProcessGone(id string) bool {
	p := strings.SplitN(id, "-", 2)
	if len(p) != 2 || p[0] == "" {
		return false
	}
	for _, c := range p[0] {
		if c < '0' || c > '9' {
			return false
		}
	}
	_, err := os.Stat("/proc/" + p[0])
	return os.IsNotExist(err)
}
func costTxn(fn func(*costState, costPolicy) error) error {
	costMutex.Lock()
	defer costMutex.Unlock()
	d := costDir()
	f, e := os.OpenFile(filepath.Join(d, "ledger.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	if e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX); e != nil {
		return e
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	var p costPolicy
	b, e := os.ReadFile(filepath.Join(d, "policy.json"))
	if e != nil {
		return e
	}
	if e = json.Unmarshal(b, &p); e != nil {
		return e
	}
	if p.Daily <= 0 || p.Monthly <= 0 || p.MaxConcurrent < 1 {
		return fmt.Errorf("invalid LLM policy")
	}
	s := costState{Stats: map[string]*costStat{}, Pending: map[string]costReservation{}, Cooldown: map[string]int64{}}
	b, e = os.ReadFile(filepath.Join(d, "usage.json"))
	if e == nil {
		if e = json.Unmarshal(b, &s); e != nil {
			return e
		}
	} else if !os.IsNotExist(e) {
		return e
	}
	// Orphan reservations remain charged conservatively after timeout/crash.
	for k, r := range s.Pending {
		if time.Now().Unix()-r.Started > 7200 || reservationProcessGone(k) {
			key := r.Day + "|" + r.Task + "|" + r.Model
			if s.Stats[key] == nil {
				s.Stats[key] = &costStat{}
			}
			s.Stats[key].Estimated += r.Tokens
			s.Stats[key].Errors++
			delete(s.Pending, k)
		}
	}
	result := fn(&s, p)
	if e = costAtomic(filepath.Join(d, "usage.json"), s, 0640); e != nil {
		return e
	}
	return result
}
func costTotals(s *costState, day string) (daily, monthly int64) {
	for k, v := range s.Stats {
		n := v.Actual + v.Estimated
		if strings.HasPrefix(k, day+"|") {
			daily += n
		}
		if strings.HasPrefix(k, day[:7]) {
			monthly += n
		}
	}
	for _, r := range s.Pending {
		if r.Day == day {
			daily += r.Tokens
		}
		if strings.HasPrefix(r.Day, day[:7]) {
			monthly += r.Tokens
		}
	}
	return
}
func costBegin(model, purpose string, n int64) (string, error) {
	id := fmt.Sprintf("%d-%d", os.Getpid(), time.Now().UnixNano())
	day := costDay()
	err := costTxn(func(s *costState, p costPolicy) error {
		if s.Cooldown[model] > time.Now().Unix() {
			return fmt.Errorf("LLM_GOVERNANCE_COOLDOWN: provider cooling down")
		}
		active := 0
		for _, r := range s.Pending {
			if r.Model == model && time.Now().Unix()-r.Started < 7200 {
				active++
			}
		}
		if active >= p.MaxConcurrent {
			return fmt.Errorf("LLM_GOVERNANCE_BUSY: concurrency limit")
		}
		filtered := *s
		filtered.Stats = map[string]*costStat{}
		filtered.Pending = map[string]costReservation{}
		unlimited := func(id string) bool {
			for _, v := range p.UnlimitedTokenModels {
				if v == id {
					return true
				}
			}
			return false
		}
		for key, value := range s.Stats {
			parts := strings.Split(key, "|")
			if !unlimited(parts[len(parts)-1]) {
				filtered.Stats[key] = value
			}
		}
		for key, value := range s.Pending {
			if !unlimited(value.Model) {
				filtered.Pending[key] = value
			}
		}
		daily, monthly := costTotals(&filtered, day)
		if !unlimited(model) && (daily+n > p.Daily || monthly+n > p.Monthly) {
			return fmt.Errorf("LLM_GOVERNANCE_BUDGET: token budget exhausted")
		}
		s.Pending[id] = costReservation{day, purpose, model, n, time.Now().Unix()}
		return nil
	})
	return id, err
}
func costFinish(id string, u *types.TokenUsage, err error) error {
	return costTxn(func(s *costState, p costPolicy) error {
		r, ok := s.Pending[id]
		if !ok {
			return nil
		}
		key := r.Day + "|" + r.Task + "|" + r.Model
		if s.Stats[key] == nil {
			s.Stats[key] = &costStat{}
		}
		v := s.Stats[key]
		v.Calls++
		if u != nil && (u.TotalTokens > 0 || u.PromptTokens+u.CompletionTokens > 0) {
			total := u.TotalTokens
			if total < u.PromptTokens+u.CompletionTokens {
				total = u.PromptTokens + u.CompletionTokens
			}
			v.Actual += int64(total)
			v.Input += int64(u.PromptTokens)
			v.Output += int64(u.CompletionTokens)
		} else {
			v.Estimated += r.Tokens
		}
		if err != nil {
			v.Errors++
			e := strings.ToLower(err.Error())
			if strings.Contains(e, "429") || strings.Contains(e, "rate limit") {
				s.Cooldown[r.Model] = time.Now().Add(15 * time.Minute).Unix()
			}
		}
		delete(s.Pending, id)
		return nil
	})
}

type costChat struct{ inner Chat }

func (w *costChat) GetModelID() string   { return w.inner.GetModelID() }
func (w *costChat) GetModelName() string { return w.inner.GetModelName() }
func costRequest(ctx context.Context, model string, m []Message, o *ChatOptions) (string, int64, *ChatOptions, string) {
	p, _ := types.LLMCallMetadataFromContext(ctx)
	if p == "" {
		p = "interactive_or_other"
	}
	opts := ChatOptions{}
	if o != nil {
		opts = *o
	}
	if opts.CompletionBudget() <= 0 {
		opts.MaxTokens = 2048
	}
	tenant, _ := types.TenantIDFromContext(ctx)
	raw, _ := json.Marshal(struct {
		Tenant   uint64
		Model    string
		Messages []Message
		Options  ChatOptions
	}{tenant, model, m, opts})
	sum := sha256.Sum256(raw)
	// Conservative UTF-8 byte bound plus output allowance; image requests are not cached.
	return p, int64(len(raw)*2 + opts.CompletionBudget()), &opts, hex.EncodeToString(sum[:])
}
func (w *costChat) Chat(ctx context.Context, m []Message, o *ChatOptions) (*types.ChatResponse, error) {
	p, n, opts, key := costRequest(ctx, w.GetModelID(), m, o)
	cache := filepath.Join(costDir(), "cache", key+".json")
	if p == "document_summary" {
		if b, e := os.ReadFile(cache); e == nil {
			var r types.ChatResponse
			if json.Unmarshal(b, &r) == nil && r.Content != "" {
				e = costTxn(func(s *costState, _ costPolicy) error {
					k := costDay() + "|" + p + "|" + w.GetModelID()
					if s.Stats[k] == nil {
						s.Stats[k] = &costStat{}
					}
					s.Stats[k].CacheHits++
					return nil
				})
				if e != nil {
					return nil, e
				}
				r.Usage = types.TokenUsage{}
				return &r, nil
			}
		}
	}
	id, e := costBegin(w.GetModelID(), p, n)
	if e != nil {
		return nil, e
	}
	r, e := w.inner.Chat(ctx, m, opts)
	var u *types.TokenUsage
	if r != nil {
		u = &r.Usage
	}
	if fe := costFinish(id, u, e); fe != nil {
		return nil, fmt.Errorf("LLM usage persistence failed: %w", fe)
	}
	if e == nil && r != nil && r.Content != "" && r.FinishReason != "length" && p == "document_summary" {
		_ = costAtomic(cache, r, 0600)
	}
	return r, e
}
func (w *costChat) ChatStream(ctx context.Context, m []Message, o *ChatOptions) (<-chan types.StreamResponse, error) {
	p, n, opts, _ := costRequest(ctx, w.GetModelID(), m, o)
	id, e := costBegin(w.GetModelID(), p, n)
	if e != nil {
		return nil, e
	}
	ch, e := w.inner.ChatStream(ctx, m, opts)
	if e != nil || ch == nil {
		_ = costFinish(id, nil, e)
		return ch, e
	}
	out := make(chan types.StreamResponse)
	go func() {
		defer close(out)
		var u *types.TokenUsage
		var endErr error
		defer func() { _ = costFinish(id, u, endErr) }()
		for {
			select {
			case <-ctx.Done():
				endErr = ctx.Err()
				return
			case r, ok := <-ch:
				if !ok {
					return
				}
				if r.Usage != nil {
					copy := *r.Usage
					u = &copy
				}
				select {
				case out <- r:
				case <-ctx.Done():
					endErr = ctx.Err()
					return
				}
			}
		}
	}()
	return out, nil
}
func wrapCost(c Chat, remote bool, err error) (Chat, error) {
	if err != nil || c == nil || !remote || costDir() == "" {
		return c, err
	}
	return &costChat{c}, nil
}
