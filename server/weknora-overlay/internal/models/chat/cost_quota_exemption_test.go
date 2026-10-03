//go:build linux
package chat
import("os";"path/filepath";"testing";"strings")
func TestUnlimitedModelStillRespectsRateControls(t *testing.T){
 d:=t.TempDir();t.Setenv("WEKNORA_LLM_COST_DIR",d)
 os.WriteFile(filepath.Join(d,"policy.json"),[]byte(`{"daily_tokens":100,"monthly_tokens":1000,"max_concurrent":1,"unlimited_token_models":["hy3"]}`),0600)
 id,err:=costBegin("hy3","summary",2000);if err!=nil {t.Fatal(err)}
 if _,err=costBegin("hy3","summary",2000);err==nil || !strings.Contains(err.Error(),"BUSY"){t.Fatalf("expected concurrency guard: %v",err)}
 if err=costFinish(id,nil,nil);err!=nil {t.Fatal(err)}
 if _,err=costBegin("glm","summary",50);err!=nil {t.Fatalf("HY3 must not consume GLM budget: %v",err)}
 if _,err=costBegin("other","summary",150);err==nil || !strings.Contains(err.Error(),"BUDGET"){t.Fatalf("GLM budget guard missing: %v",err)}
}
func TestReservationProcessGone(t *testing.T){
 if !reservationProcessGone("2147483647-1"){t.Fatal("dead PID must be recoverable")}
 if reservationProcessGone("external-1"){t.Fatal("unknown reservation ID must remain protected")}
}
