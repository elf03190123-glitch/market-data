// 렌더링 검증 (아티팩트 도구 verify.js를 사이트용으로 옮긴 것)
// 사용: (npm i xlsx@0.18.5 playwright@1 pngjs@7 설치된 폴더에서)
//   node <이 파일> <region 페이지 URL> <출력 폴더> [비교할 원본 HTML 경로]
//   예) python -m http.server 8765 --bind 127.0.0.1 (site/에서) 후
//       node verify.js http://127.0.0.1:8765/region/ ./out ../artifacts/.../index.html
// 확인: 페이지 오류 없음 · h1/요약/하단 · 4개 엑셀(시트 10개, 최신 월 지역국가·도시 여객 합 = 포털 합계)
//       · '엑셀 내려받기' 버튼이 실제 다운로드(Blob + a.download)를 만드는지 · 스크린샷(원본과 픽셀 비교)
const {createRequire}=require('module');const req=createRequire(process.cwd()+'/');
const {chromium}=req('playwright');const fs=require('fs'),path=require('path');
(async()=>{
 const url=process.argv[2],out=process.argv[3]||'.',orig=process.argv[4];
 fs.mkdirSync(out,{recursive:true});
 const b=await chromium.launch();
 const ctx=await b.newContext({viewport:{width:1280,height:900},acceptDownloads:true});
 const p=await ctx.newPage();const errs=[];
 p.on('pageerror',e=>errs.push(e.message));
 p.on('console',m=>{if(m.type()==='error'&&!/fonts\.(googleapis|gstatic)/.test((m.location()||{}).url||''))errs.push('console: '+m.text());});
 await p.route(/fonts\.(googleapis|gstatic)/,r=>r.abort());
 await p.goto(url);await p.waitForFunction(()=>window.__ENV&&document.getElementById('kpis').children.length>0,null,{timeout:20000});
 await p.waitForTimeout(1500);
 const info=await p.evaluate(()=>({h1:document.querySelector('h1').innerText,note1:document.getElementById('note1').innerText.slice(0,120),
   foot:document.querySelector('footer').innerText,kpiYears:[...document.querySelectorAll('#seg-kpiyear button')].map(x=>x.innerText),
   as_of:window.__ENV.meta.as_of}));
 console.log(JSON.stringify(info,null,1));
 await p.screenshot({path:path.join(out,'new.png'),fullPage:true});
 await p.addScriptTag({path:req.resolve('xlsx/dist/xlsx.full.min.js')});
 let ok=true;
 for(const v of ['dep_mix','dep_paid','arr_mix','arr_paid']){
  const r=await p.evaluate((v)=>window.__buildXlsx(v).then(buf=>{const wb=XLSX.read(buf);
    const ms=window.__ENV.data.DATA[v].months,ym=+ms[ms.length-1];
    const rows=n=>XLSX.utils.sheet_to_json(wb.Sheets[n],{header:1});
    const sum=(n,c,f)=>rows(n).filter(x=>f(x[0])).reduce((t,x)=>t+(x[c]||0),0);
    const all=ms.reduce((t,_,i)=>t+window.__ENV.data.DATA[v].total.pass[i],0);
    return {sheets:wb.SheetNames.length,names:wb.SheetNames,A:sum('지역국가_월별',5,x=>x===ym),C:sum('도시별_월별',4,x=>x===ym),
      D:sum('국내선_월별',4,x=>x===ym),Aall:sum('지역국가_월별',5,x=>typeof x==='number'),allData:all,
      t1:rows('1_월별총계').slice(-1)[0],h3:rows('3_국가별_연간')[0].slice(-4),guide:rows('0_안내').map(x=>x.join(' : ')).slice(0,8)};}),v);
  const api=await p.evaluate(v=>window.__ENV.data.xmeta[v].p,v);
  const good=r.sheets===10&&r.A===api&&r.C===api&&r.Aall===r.allData;
  ok=ok&&good;
  console.log(v,good?'OK':'FAIL',JSON.stringify({sheets:r.sheets,A:r.A,C:r.C,D:r.D,API:api,Aall:r.Aall,allData:r.allData,t1:r.t1,h3:r.h3}));
  if(v==='dep_mix')console.log(' 0_안내:',JSON.stringify(r.guide));
 }
 // 실제 버튼 → 다운로드
 await p.click('#xlsxbtn');await p.waitForTimeout(300);
 const [dl]=await Promise.all([p.waitForEvent('download',{timeout:30000}),p.click('#xlsxpanel button[data-xv="dep_paid"]')]);
 const fp=path.join(out,dl.suggestedFilename());await dl.saveAs(fp);
 console.log('DOWNLOAD',dl.suggestedFilename(),fs.statSync(fp).size,'bytes');
 // 주요 전환 클릭(오류만 확인): 방향·기준·연도·연간 전체·KPI 전체 보기
 const yrs=await p.evaluate(()=>[...document.querySelectorAll('#seg-natyear button')].map(x=>x.dataset.natyear));
 const fy=yrs[yrs.length-2];
 for(const sel of ['.sticky-basis button[data-dir="arr"]','.sticky-basis button[data-basis="mix"]',`#seg-natyear button[data-natyear="${fy}"]`,'#seg-natperiod button[data-natperiod="full"]',
   `#seg-cityyear button[data-cityyear="${fy}"]`,'#seg-cityperiod button[data-cityperiod="full"]',`#seg-cmpyear button[data-cmpyear="${fy}"]`,
   `#seg-cmp2year button[data-cmp2year="${fy}"]`,'#seg-cmp2period button[data-cmp2period="full"]','#seg-areaperiod button[data-areaperiod="full"]',
   '#kpi-showall','button[data-view="cont"]','.sticky-basis button[data-dir="dep"]']){await p.click(sel);await p.waitForTimeout(150);}
 console.log('AFTER CLICKS',JSON.stringify(await p.evaluate(()=>({nat:document.getElementById('t-nat').innerText,city:document.getElementById('t-city').innerText,
   cmp4:document.getElementById('t-cmp4').innerText,area:document.getElementById('t-areaytd-title').innerText,kpiall:document.querySelectorAll('#kpiall .kpiyear-lab').length}))));
 // 문서 만들기 → Word(.docx) 다운로드(JSZip CDN 필요)
 await p.click('#docbtn');await p.waitForTimeout(300);
 try{const [d2]=await Promise.all([p.waitForEvent('download',{timeout:60000}),p.click('#docdxbtn')]);
   console.log('DOCX',d2.suggestedFilename());}catch(e){console.log('DOCX FAIL',e.message.split('\n')[0]);ok=false;}
 console.log('PAGE ERRORS',JSON.stringify(errs));
 if(orig){
  const q=await ctx.newPage();await q.route(/fonts\.(googleapis|gstatic)/,r=>r.abort());
  await q.goto('file://'+path.resolve(orig));await q.waitForTimeout(2500);
  await q.screenshot({path:path.join(out,'original.png'),fullPage:true});
  const {PNG}=req('pngjs');
  const A=PNG.sync.read(fs.readFileSync(path.join(out,'new.png'))),B=PNG.sync.read(fs.readFileSync(path.join(out,'original.png')));
  const W=Math.min(A.width,B.width),H=Math.min(A.height,B.height);let diff=0;
  const D=new PNG({width:W,height:H});
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){const i=(A.width*y+x)*4,j=(B.width*y+x)*4,k=(W*y+x)*4;
    const d=Math.abs(A.data[i]-B.data[j])+Math.abs(A.data[i+1]-B.data[j+1])+Math.abs(A.data[i+2]-B.data[j+2]);
    if(d>30){diff++;D.data[k]=255;D.data[k+1]=0;D.data[k+2]=0;D.data[k+3]=255;}else{D.data[k]=B.data[j];D.data[k+1]=B.data[j+1];D.data[k+2]=B.data[j+2];D.data[k+3]=60;}}
  fs.writeFileSync(path.join(out,'diff.png'),PNG.sync.write(D));
  console.log('SCREENSHOT new',A.width+'x'+A.height,'original',B.width+'x'+B.height,'diff pixels',diff,(diff/(W*H)*100).toFixed(3)+'%');
 }
 await b.close();process.exit(ok&&!errs.length?0:1);
})().catch(e=>{console.error(e);process.exit(2);});
