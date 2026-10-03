package openaicompletions

import (
	"context"
	"encoding/json"
	"github.com/Tencent/WeKnora/internal/models/api"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestExplicitNonstreamContractForApp(t *testing.T) {
	t.Setenv("SSRF_WHITELIST", "127.0.0.1")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		json.NewDecoder(r.Body).Decode(&body)
		if body["stream"] != false {
			t.Errorf("upstream stream must be false: %v", body["stream"])
		}
		if _, ok := body["stream_options"]; ok {
			t.Error("stream options must not be sent")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"choices":[{"message":{"content":"连接成功"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":4,"total_tokens":9}}`))
	}))
	defer server.Close()
	settings := api.DefaultOpenAICompletions()
	settings.ExtraBody = map[string]interface{}{"stream": false}
	c := New(Config{Endpoint: api.Endpoint{BaseURL: server.URL + "/v1", Model: "hy4-preview"}, Settings: settings})
	ch, err := c.ChatStream(context.Background(), []api.Message{{Role: "user", Content: "你好"}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	response, ok := <-ch
	if !ok || response.Content != "连接成功" || !response.Done || response.Usage == nil {
		t.Fatalf("invalid app response: %+v", response)
	}
	if _, ok := <-ch; ok {
		t.Error("stream must terminate")
	}
}
