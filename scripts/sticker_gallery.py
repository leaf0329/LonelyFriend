from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageOps


def write_gallery(directory: Path, catalog: dict) -> None:
    thumbnails = directory / "thumbnails"
    thumbnails.mkdir(exist_ok=True)
    for item in catalog["stickers"]:
        target = thumbnails / (Path(item["file"]).stem + ".jpg")
        if target.is_file():
            continue
        with Image.open(directory / "files" / item["file"]) as image:
            image.seek(getattr(image, "n_frames", 1) // 2)
            frame = ImageOps.contain(image.convert("RGBA"), (170, 170))
            canvas = Image.new("RGB", (170, 170), "white")
            canvas.paste(frame, ((170 - frame.width) // 2, (170 - frame.height) // 2), frame)
            canvas.save(target, quality=82)
    # JSON is embedded as inert data; upstream captions must not close the script tag.
    data = json.dumps(catalog, ensure_ascii=False).replace("<", "\\u003c")
    page = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>大肥鱼表情库 · 本地审核</title>
<style>
:root{color-scheme:light;font-family:"Microsoft YaHei",system-ui,sans-serif;color:#203449;background:#f2f6fb}
*{box-sizing:border-box}body{margin:0}header,main,footer{max-width:1440px;margin:auto;padding:24px}
header{padding-bottom:12px}h1{margin:0 0 12px;font-size:28px}p{line-height:1.7;margin:8px 0;color:#53677e}
.toolbar{position:sticky;top:0;z-index:2;background:#f2f6fbf5;backdrop-filter:blur(8px);border-bottom:1px solid #d9e3ef}
.controls{max-width:1440px;margin:auto;padding:12px 24px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,select,button{font:inherit;border:1px solid #c3d1e3;border-radius:8px;padding:9px 11px;background:white;color:#203449}
input{flex:1;min-width:220px}button{cursor:pointer}button:hover{background:#eaf2ff}.export{background:#2362bc;color:white}.export:hover{background:#174c99}
#count{font-size:14px;color:#52657a;white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px}
.card{background:white;border-radius:12px;border:1px solid #dce5f0;overflow:hidden;display:flex;flex-direction:column}
.picture{height:190px;display:flex;align-items:center;justify-content:center;border:0;border-radius:0;background:#fff;padding:10px}
.picture img{max-width:170px;max-height:170px;object-fit:contain}.info{padding:12px;display:flex;flex-direction:column;gap:9px;flex:1}
.title{font-size:15px;font-weight:600;line-height:1.5}.tags{font-size:12px;line-height:1.6;color:#60758d}.badge{font-size:12px;align-self:flex-start;padding:3px 7px;border-radius:5px;background:#eaf4ee;color:#286645}
.reserved .badge{background:#fff1dd;color:#92651a}.manual-exclude{border-color:#ca7b7b}.manual-keep{border-color:#499975}.reason{font-size:12px;line-height:1.6;color:#8c6125}
.choice{margin-top:auto;font-size:13px;width:100%}.tiny{color:#70839a;font-size:12px}footer{font-size:13px;line-height:1.9}a{color:#2464b7}
dialog{border:0;border-radius:14px;padding:20px;max-width:min(95vw,1000px);max-height:95vh;overflow:auto;color:#203449;box-shadow:0 16px 70px #0004}
dialog::backdrop{background:#102131aa}.modal-image{display:block;max-width:100%;max-height:65vh;margin:16px auto;object-fit:contain}.modal-head{display:flex;justify-content:space-between;gap:20px;align-items:center}.modal-head h2{font-size:20px;margin:0}.modal-detail{white-space:pre-wrap;font-size:14px;line-height:1.8;color:#52657a}#notice{color:#9b3c2f}
</style>
<header><h1>大肥鱼表情库 · 本地待审版</h1>
<p id="summary"></p><p>点击缩略图可看原图或播放动图。搜索支持编号、图中文字含义、情绪和标签。这里的“初审可用”是第一轮画面审核结果，最终以你的选择为准。</p>
<p>你的“手动保留 / 手动排除”只保存在当前浏览器；审核后点“导出我的审核”。页面不上传图片或结果，不连接机器人。GIF 初审检查首、中、尾帧，未逐帧确认。</p><p id="notice" role="status"></p></header>
<div class="toolbar"><div class="controls"><input id="search" placeholder="搜索：晚安、吃饭、抽象、k0115…" aria-label="搜索表情">
<select id="filter" aria-label="审核筛选"><option value="all">全部图片</option><option value="pending">待我审核</option><option value="initial">初审可用</option><option value="reserved">仅收藏</option><option value="keep">我保留的</option><option value="exclude">我排除的</option></select>
<button id="export" class="export">导出我的审核</button><span id="count"></span></div></div>
<main><div class="grid" id="grid"></div></main>
<footer>非商业个人使用；原图及作者署名保留。来源：<a href="https://kiriyuki.com" target="_blank" rel="noreferrer">可以叫我云大人 / 雾雪</a>（<a href="https://space.bilibili.com/103739" target="_blank" rel="noreferrer">B站作者</a>）、<a href="https://fisharchive.cc" target="_blank" rel="noreferrer">蓝色大肥鱼档案馆</a>（图片权利归各原作者）。<br>
目录及标签参考 <a href="https://github.com/Loping151/dsh-nekowhale-stickers" target="_blank" rel="noreferrer">dsh-nekowhale-stickers</a>，适用 CC BY-NC 4.0 及原图单独条款；档案馆说明见<a href="https://fisharchive.cc/about" target="_blank" rel="noreferrer">版权页面</a>。未重绘或压缩原图；缩略图仅用于浏览。</footer>
<dialog id="detail"><div class="modal-head"><h2 id="detail-title"></h2><button id="close">关闭 · Esc</button></div><img id="original" class="modal-image" alt="表情原图"><div id="detail-text" class="modal-detail"></div></dialog>
<script id="catalog" type="application/json">__CATALOG__</script>
<script>
'use strict';
const catalog=JSON.parse(document.getElementById('catalog').textContent), items=catalog.stickers;
const key='lonelyfriend-sticker-review-v1', notice=document.getElementById('notice');
let choices={};
try{choices=JSON.parse(localStorage.getItem(key)||JSON.stringify(catalog.manual_review?.choices||{}))}catch(error){notice.textContent='浏览器无法读取本地审核记录；请在离开前导出审核结果。'}
const node=(tag,cls,text)=>{const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el};
const grid=document.getElementById('grid'), cards=new Map(), search=document.getElementById('search'), filter=document.getElementById('filter');
const approved=items.filter(x=>x.review.status==='approved').length;
document.getElementById('summary').textContent=`共 ${items.length} 张，其中 ${items.filter(x=>x.file.endsWith('.gif')).length} 张 GIF；初审可用 ${approved} 张，仅收藏 ${items.length-approved} 张。`;
if(catalog.manual_review){document.querySelector('h1').textContent='大肥鱼表情库 · 人工审核已应用';document.getElementById('summary').textContent+=` 已应用你的审核：可用 ${catalog.manual_review.available} 张，排除 ${items.length-catalog.manual_review.available} 张。`;}
function save(){try{localStorage.setItem(key,JSON.stringify(choices));notice.textContent='选择已保存在当前浏览器；完成后请导出一份审核结果。'}catch(error){notice.textContent='浏览器不能保存选择，请立即导出审核结果以免丢失。'}}
function update(){const query=search.value.trim().toLocaleLowerCase(), mode=filter.value;let count=0;
for(const item of items){const record=cards.get(item.id), choice=choices[item.id], initial=item.review.status==='approved';
const hit=record.text.includes(query)&&(mode==='all'||(mode==='pending'&&!choice)||(mode==='initial'&&initial)||(mode==='reserved'&&!initial)||mode===choice);
record.card.hidden=!hit;record.card.style.display=hit?'':'none';if(hit)count++;
record.card.classList.toggle('manual-keep',choice==='keep');record.card.classList.toggle('manual-exclude',choice==='exclude');record.select.value=choice||'';}
document.getElementById('count').textContent=`显示 ${count} / ${items.length} · 已选 ${Object.keys(choices).length} 张`;}
for(const item of items){const reserved=item.review.status!=='approved', card=node('article','card'+(reserved?' reserved':''));
const button=node('button','picture'), img=node('img');img.src='thumbnails/'+item.file.replace(/\.[^.]+$/,'.jpg');img.loading='lazy';img.alt=item.name;button.append(img);button.title='查看原图：'+item.id;
button.addEventListener('click',()=>{document.getElementById('detail-title').textContent=item.id+' · '+item.name;document.getElementById('original').src='files/'+item.file;
document.getElementById('detail-text').textContent='标签：'+item.tags.join(' / ')+'\n说明：'+item.description+'\n初审：'+(reserved?'仅收藏':'可用')+(item.review.reason?'\n原因：'+item.review.reason:'')+'\n来源：'+catalog.sources[item.source].name;document.getElementById('detail').showModal();});
const info=node('div','info');info.append(node('span','badge',reserved?'仅收藏':'初审可用'),node('div','title',item.name),node('div','tiny',item.id+' · '+item.emotion),node('div','tags',item.tags.join(' / ')));
if(item.review.reason)info.append(node('div','reason',item.review.reason));
const select=node('select','choice');select.setAttribute('aria-label',item.id+' 的人工审核');
for(const [value,label] of [['',catalog.manual_review?.unmarked==='keep'?'未标记 · 默认保留':'按初审 · 尚未手审'],['keep','✓ 我确认保留'],['exclude','× 排除自动使用']]){const option=node('option','',label);option.value=value;select.append(option)}
select.addEventListener('change',()=>{if(select.value)choices[item.id]=select.value;else delete choices[item.id];save();update()});info.append(select);card.append(button,info);grid.append(card);
cards.set(item.id,{card,select,text:[item.id,item.name,item.emotion,...item.tags,item.description].join(' ').toLocaleLowerCase()});}
search.addEventListener('input',update);filter.addEventListener('change',update);document.getElementById('close').addEventListener('click',()=>document.getElementById('detail').close());
document.getElementById('detail').addEventListener('close',()=>document.getElementById('original').removeAttribute('src'));
document.getElementById('export').addEventListener('click',()=>{const result={schema:1,collected_on:catalog.collected_on,exported_at:new Date().toISOString(),unmarked:catalog.manual_review?.unmarked||'initial',choices};
const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json;charset=utf-8'}));const a=node('a');a.href=url;a.download='大肥鱼-人工审核结果.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});update();
</script></html>'''
    (directory / "gallery.html").write_text(page.replace("__CATALOG__", data), encoding="utf-8")
