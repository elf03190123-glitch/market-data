# 요약 노트(note1/note3)와 07 정리(finds 6개)를 데이터에서 자동 생성 (한/영)
# 아티팩트 도구 narr.py 그대로 + 1월(N=1) 문구 처리만 추가
MON_EN=['January','February','March','April','May','June','July','August','September','October','November','December']
NAT_EN={'필리핀':'Philippines','태국':'Thailand','말레이시아':'Malaysia','베트남':'Vietnam','괌':'Guam','일본':'Japan','중국':'China',
        '미국':'US','캐나다':'Canada','영국':'UK'}
AREA_EN={'일본':'Japan','중국':'China','아시아':'Asia','미주':'the Americas','유럽':'Europe','대양주':'Oceania','기타':'Other'}
SEA=['필리핀','태국','말레이시아','베트남','괌']

def pct(a,b):
    return None if not b else (a/b-1)*100
def sp(x,d=1):  # 부호 포함 %
    return '—' if x is None else ('+' if x>=0 else '')+f'{x:.{d}f}%'
def man(n):     # 1,065만
    return f'{round(n/1e4):,}만'
def mil(n):     # 10.65 million / 400,000
    return f'{n/1e6:.2f} million' if n>=1e6 else f'{round(n/1e4)*1e4:,.0f}'
def manton(n):  # 149만 톤
    return f'{round(n/1e4):,}만 톤'

def ytd_nat(d,name,y):
    for x in d['topNations']:
        if x['name']==name: return x['y%d'%y]
    return 0

def monthly_yoy(d,LY,N):
    ms=d['months']; idx={m:i for i,m in enumerate(ms)}; out=[]
    for m in range(1,N+1):
        a='%d%02d'%(LY,m); b='%d%02d'%(LY-1,m)
        if a in idx and b in idx:
            out.append((m,pct(d['total']['pass'][idx[a]],d['total']['pass'][idx[b]])))
    return out

def gen(DATA):
    ms=DATA['dep_mix']['months']; last=ms[-1]; LY=int(last[:4]); N=int(last[4:]); Y0=int(ms[0][:4])
    PAX={'dep_mix':('여객','passengers'),'dep_paid':('유임여객','paid passengers'),
         'arr_mix':('도착 여객','arrival passengers'),'arr_paid':('도착 유임여객','arrival paid passengers')}
    DIRK={'dep':('출발','departure'),'arr':('도착','arrival')}
    per_ko=f'1~{N}월' if N>1 else '1월'; per_en=f'January–{MON_EN[N-1]}' if N>1 else 'January'
    KO={}; EN={}
    for v,d in DATA.items():
        dk,bk=v.split('_'); pk,pe=PAX[v]
        y=d['ytd']; cur=y[str(LY)]; prv=y[str(LY-1)]; base=y[str(Y0)]
        g3=pct(cur['pass'],base['pass']); g1=pct(cur['pass'],prv['pass'])
        my=monthly_yoy(d,LY,N)
        hi=max(my,key=lambda x:x[1]); lo=min(my,key=lambda x:x[1]); ls=my[-1]
        jp=ytd_nat(d,'일본',LY); cn=ytd_nat(d,'중국',LY)
        share=(jp+cn)/cur['pass']*100
        jpy=pct(jp,ytd_nat(d,'일본',LY-1)); cny=pct(cn,ytd_nat(d,'중국',LY-1))
        cnm=cn/ytd_nat(d,'중국',Y0) if ytd_nat(d,'중국',Y0) else None
        # note1
        swing_ko=f'전년동월 대비 증가율은 {hi[0]}월 {hi[1]:.1f}%가 가장 높았고 {lo[0]}월 {lo[1]:.1f}%가 가장 낮았으며, 최근 {ls[0]}월은 {ls[1]:.1f}%입니다.'
        swing_en=f'Year-over-year growth peaked at {hi[1]:.1f}% in {MON_EN[hi[0]-1]} and bottomed at {lo[1]:.1f}% in {MON_EN[lo[0]-1]}; the latest month ({MON_EN[ls[0]-1]}) was {ls[1]:.1f}%.'
        if N==1:  # 1월(연도 첫 달)은 비교할 달이 하나뿐
            swing_ko=f'1월 전년동월 대비 증가율은 {ls[1]:.1f}%입니다.'
            swing_en=f'Year-over-year growth in January was {ls[1]:.1f}%.'
        note1=f'{pk}은 {LY}년 {per_ko} 누계 <b>{cur["pass"]:,}</b>명으로 {Y0}년 같은 기간보다 <b>{sp(g3)}</b> 늘었습니다. '+swing_ko
        note1e=f'{pe[0].upper()+pe[1:]} reached a cumulative <b>{cur["pass"]:,}</b> for {per_en} {LY}, <b>{sp(g3)}</b> versus the same period of {Y0}. '+swing_en
        note3=f'전체 성장은 <b>일본과 중국</b>이 이끌고 있습니다. {LY}년 {per_ko} 기준 두 시장이 국제선 {DIRK[dk][0]} {pk.replace("도착 ","")}의 <b>{share:.1f}%</b>를 차지합니다.'
        note3e=f'Growth is led by <b>Japan and China</b>. For {per_en} {LY} the two markets account for <b>{share:.1f}%</b> of international {DIRK[dk][1]} {pe.replace("arrival ","")}.'
        # finds 1
        f1=[sp(g1),f'{pk} 전년비',f'<b>{per_ko} 누계 기준 전년 대비 {sp(g1)}입니다.</b> 전년동월 대비는 {hi[0]}월 {hi[1]:.1f}%(최고) · {lo[0]}월 {lo[1]:.1f}%(최저) · {ls[0]}월 {ls[1]:.1f}%(최근)로 움직였습니다. 특정 달의 성장률을 연간 계획에 그대로 연장하지 않는 편이 안전합니다.']
        f1e=[sp(g1),f'{pe[0].upper()+pe[1:]} YoY',f'<b>{per_en} is {sp(g1)} year over year.</b> Monthly YoY moved between {hi[1]:.1f}% ({MON_EN[hi[0]-1]}, high), {lo[1]:.1f}% ({MON_EN[lo[0]-1]}, low) and {ls[1]:.1f}% ({MON_EN[ls[0]-1]}, latest). It is safer not to extend any single month’s rate into annual planning.']
        if N==1:
            f1[2]=f'<b>1월 기준 전년 대비 {sp(g1)}입니다.</b> 한 달 치 성장률을 연간 계획에 그대로 연장하지 않는 편이 안전합니다.'
            f1e[2]=f'<b>January is {sp(g1)} year over year.</b> It is safer not to extend a single month’s rate into annual planning.'
        # finds 2
        mt_ko=f' 중국은 {Y0}년 같은 기간 대비 {cnm:.1f}배입니다.' if cnm else ''
        mt_en=f' China is {cnm:.1f} times its {Y0} level.' if cnm else ''
        f2=[f'{share:.1f}%','일본+중국 비중',f'<b>성장이 두 시장에 몰려 있습니다.</b> 일본 {man(jp)} 명({sp(jpy)}), 중국 {man(cn)} 명({sp(cny)}).'+mt_ko]
        f2e=[f'{share:.1f}%','Japan + China share',f'<b>Growth is concentrated in two markets.</b> Japan {mil(jp)} ({sp(jpy)}), China {mil(cn)} ({sp(cny)}).'+mt_en]
        # finds 3
        sea=[(n,pct(ytd_nat(d,n,LY),ytd_nat(d,n,LY-1))) for n in SEA]
        sea=[x for x in sea if x[1] is not None]; sea.sort(key=lambda x:x[1])
        allneg=all(x[1]<0 for x in sea)
        lst=', '.join(f'{n} {sp(p)}' for n,p in sea); lste=', '.join(f'{NAT_EN[n]} {sp(p)}' for n,p in sea)
        f3=[sp(sea[0][1]),f'{sea[0][0]} · 동남아',('<b>동남아·괌 근거리 시장은 역성장입니다.</b> ' if allneg else '<b>동남아·괌 근거리 시장은 국가별로 엇갈립니다.</b> ')+lst+'.']
        f3e=[sp(sea[0][1]),f'{NAT_EN[sea[0][0]]} / SE Asia',('<b>Southeast Asia and Guam are in decline.</b> ' if allneg else '<b>Southeast Asia and Guam are mixed.</b> ')+lste+'.']
        # finds 4
        if bk=='mix':
            eu=pct(d['areaYtd']['유럽'][str(LY)],d['areaYtd']['유럽'][str(LY-1)]); am=pct(d['areaYtd']['미주'][str(LY)],d['areaYtd']['미주'][str(LY-1)])
            cands=[(x['name'],pct(x['y%d'%LY],x['y%d'%(LY-1)])) for x in d['topNations'] if x['area'] in('유럽','미주') and x['y%d'%LY]>=100000 and x['y%d'%(LY-1)]]
            cands.sort(key=lambda x:-x[1]); top=cands[:3]
            f4=[sp(eu),'유럽 여객',f'<b>장거리 노선:</b> 유럽 {sp(eu)}, 미주 {sp(am)}. 10만 명 이상 국가 중 증가율 상위는 '+', '.join(f'{n} {sp(p)}' for n,p in top)+'. 상당 부분이 환승객이라 02 섹션에서 유임 기준과 비교해 보세요.']
            f4e=[sp(eu),'Europe passengers',f'<b>Long-haul:</b> Europe {sp(eu)}, the Americas {sp(am)}. Fastest-growing markets above 100,000 passengers: '+', '.join(f'{NAT_EN.get(n,n)} {sp(p)}' for n,p in top)+'. Much of this is transit traffic — compare with the paid basis in Section 02.']
        else:
            mixd=DATA[dk+'_mix']
            am_mix=mixd['areaYtd']['미주'][str(LY)]; am_paid=d['areaYtd']['미주'][str(LY)]
            tr=(1-am_paid/am_mix)*100
            gm=pct(am_mix,mixd['areaYtd']['미주'][str(LY-1)]); gp=pct(am_paid,d['areaYtd']['미주'][str(LY-1)])
            um=pct(ytd_nat(mixd,'미국',LY),ytd_nat(mixd,'미국',LY-1)); up=pct(ytd_nat(d,'미국',LY),ytd_nat(d,'미국',LY-1))
            f4=[f'{tr:.1f}%','미주 환승 비중',f'<b>환승을 빼면 미주가 가장 크게 달라집니다.</b> 미주 노선 {DIRK[dk][0]} 여객의 {tr:.1f}%가 환승객이라, 유임 기준 미주 성장률은 {sp(gp)}(유임+환승 {sp(gm)}), 미국은 {sp(up)}({sp(um)})입니다.']
            f4e=[f'{tr:.1f}%','Americas transit share',f'<b>The Americas change the most once transit is excluded.</b> {tr:.1f}% of Americas {DIRK[dk][1]} passengers are transit, so on a paid basis Americas growth is {sp(gp)} (vs {sp(gm)} incl. transit) and the US {sp(up)} (vs {sp(um)}).']
        # finds 5 cargo
        cg=pct(cur['cargo'],prv['cargo'])
        ac=[(a,pct(d['areaCargoYtd'][a][str(LY)],d['areaCargoYtd'][a][str(LY-1)]),d['areaCargoYtd'][a][str(LY)]-d['areaCargoYtd'][a][str(LY-1)]) for a in d['areas']]
        ac.sort(key=lambda x:-x[2]); top=ac[:3]
        f5=[sp(cg),'화물 전년비',f'{DIRK[dk][0]} 화물 {per_ko} {manton(cur["cargo"])}({sp(cg)}). 증가량 기준 '+', '.join(f'{a} {sp(p)}' for a,p,_ in top)+'가 견인했습니다. 화물과 운항편수는 여객 기준과 무관합니다.']
        f5e=[sp(cg),'Cargo YoY',f'{DIRK[dk][1].capitalize()} cargo for {per_en}: {cur["cargo"]/1e6:.2f} million tonnes ({sp(cg)}). Largest contributors: '+', '.join(f'{AREA_EN[a]} {sp(p)}' for a,p,_ in top)+'. Cargo and flight counts do not depend on the passenger basis.']
        # finds 6 domestic
        dy=pct(cur['dpass'],prv['dpass']); d23=pct(cur['dpass'],base['dpass'])
        f6=[sp(d23),f'국내선 vs {Y0}년',f'국내선 {per_ko} {man(cur["dpass"])} 명, 전년 대비 {sp(dy)}, {Y0}년 대비 {sp(d23)}. 국내선은 환승 집계가 없어 두 기준의 수치가 같습니다.']
        f6e=[sp(d23),f'Domestic vs. {Y0}',f'Domestic {per_en}: {cur["dpass"]/1e6:.2f} million passengers, {sp(dy)} year over year, {sp(d23)} versus {Y0}. Domestic has no transit count, so both bases match.']
        KO[v]={'note1':note1,'note3':note3,'finds':[f1,f2,f3,f4,f5,f6]}
        EN[v]={'note1':note1e,'note3':note3e,'finds':[f1e,f2e,f3e,f4e,f5e,f6e]}
    return KO,EN
