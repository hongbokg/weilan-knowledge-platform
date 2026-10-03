// Private pilot converter; production WeKnora binary is not modified.
package main

import (
 "encoding/json"
 "errors"
 "flag"
 "fmt"
 "os"
 "path/filepath"
 "strings"
 anydoc "github.com/firecrawl/anydoc/go"
)

func beneath(path, root string) bool {
 real,err:=filepath.EvalSymlinks(path);if err!=nil{return false}
 rel,err:=filepath.Rel(root,real)
 return err==nil && rel!=".." && !strings.HasPrefix(rel,".."+string(os.PathSeparator))
}

func convert() error {
 input:=flag.String("input","","content-addressed archived source")
 format:=flag.String("format","","source extension")
 output:=flag.String("output","","private derived directory")
 flag.Parse()
 if !beneath(*input,"/data/archive/objects/pilot-private") || !beneath(*output,"/data/archive/derived/pilot-private") {return errors.New("outside private pilot scope")}
 switch *format {case "doc","docx","ppt","pptx":default:return errors.New("only Office documents in this pilot queue")}
 info,err:=os.Stat(*input);if err!=nil{return err}
 if info.Size()>64*1024*1024{return errors.New("oversize Office file needs a separate bounded conversion task")}
 data,err:=os.ReadFile(*input);if err!=nil{return err}
 f,ok:=anydoc.FormatFromExtension(*format);if !ok{return errors.New("unsupported format")}
 document,err:=anydoc.ToDocument(data,&f);if err!=nil{return err}
 markdown,err:=anydoc.ToMarkdownWithAssetLinks(data,&f);if err!=nil{return err}
 // Preserve the structured document and embedded asset bytes for later rendering.
 encoded,err:=json.Marshal(document);if err!=nil{return err}
 for name,bytes:=range map[string][]byte{"document.json":encoded,"document.md":[]byte(markdown)} {
  path:=filepath.Join(*output,name)
  file,err:=os.OpenFile(path+".tmp",os.O_CREATE|os.O_TRUNC|os.O_WRONLY,0600);if err!=nil{return err}
  _,err=file.Write(bytes);if err==nil{err=file.Sync()};file.Close();if err!=nil{return err}
  if err=os.Rename(path+".tmp",path);err!=nil{return err}
 }
 return json.NewEncoder(os.Stdout).Encode(map[string]any{"engine":"anydoc","version":anydoc.Version,"markdown_bytes":len(markdown),"blocks":len(document.Blocks),"assets":len(document.Assets)})
}

func main(){if err:=convert();err!=nil{fmt.Fprintln(os.Stderr,err);os.Exit(1)}}
