"""GPU OCR for original images; OCR output retains its original NAS location."""
import json,re,time
from pathlib import Path
from PIL import Image,ImageOps

def visible_text(md):
    text=re.sub(r'!\[[^\]]*\]\([^\n]*\)','',md)
    text=re.sub(r'<img\b[^>]*>','',text,flags=re.I)
    return re.sub(r'<[^>]*>|\s+','',text)

def parse_image(sync,source,revision):
    from sync import atomic,quality,Review
    base=sync.archive/'derived'/revision;out=base/'page-000001';out.mkdir(parents=True,exist_ok=True)
    qc=out/'quality.json';png=sync.ram/(revision+'-image.png');sync.ram.mkdir(parents=True,exist_ok=True)
    if not qc.exists():
        sync.stage('cpu_image',page=1,pages=1)
        with Image.open(source) as original:
            if original.width*original.height>40_000_000:raise Review('image_large_requires_tiling')
            frames=getattr(original,'n_frames',1)
            if frames!=1:raise Review('multi_frame_image_requires_split')
            image=ImageOps.exif_transpose(original).convert('RGB')
            width,height=image.size
            image.save(png,format='PNG')
        try:
            start=time.monotonic();outputs=sync.mineru(png,out);md=outputs['markdown'].read_text()
            structured=json.loads(outputs['structured_content'].read_text())
            blocks=[b for page in structured.get('pages',[]) for b in page.get('blocks',[])]
            errors=quality(md,blocks,'',False)
            if len(visible_text(md))<10:errors.append('image_ocr_no_text')
            # Images that remain image-only are not fabricated as recognized text.
            md=re.sub(r'!\[[^\]]*\]\([^\n]*\)','[原图保存在 NAS 源文件中，可通过文件目录预览]',md)
            (out/'clean.md').write_text(md)
            atomic(qc,{'errors':sorted(set(errors)),'parser':sync.cfg['mineru_version'],'location_kind':'image','image_width':width,'image_height':height,'inference_seconds':round(time.monotonic()-start,3)})
        finally:png.unlink(missing_ok=True)
    result=json.loads(qc.read_text());md=(out/'clean.md').read_text()
    atomic(base/'manifest.json',{'revision':revision,'pages':1,'image':True,'parser':sync.cfg['mineru_version'],'errors':result['errors']})
    if result['errors']:raise Review(','.join(result['errors']))
    return ['## 原始图片识别文字（不代表 PDF 页码）\n\n'+md],base
