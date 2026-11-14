# 원자료(raw) → 화면 데이터(DATA) 한 조합(dep_mix 등) 전체 재계산. 아티팩트 도구 build.py 그대로(경로 처리만 변경)
import os,sys,json,math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from raw import *; from cityarea import city_area
MERGE={'프롬펜(PNH)':'테초 국제공항(KTI)'}  # 프놈펜 공항 이전(2025) — drivers 계산 시 한 도시로 합산
def build(Rv):
    A=Rv['A'][1]; C=Rv['C'][1]; Dm=Rv['D'][1]
    ms=sorted({r[0] for r in A}); idx={m:i for i,m in enumerate(ms)}; n=len(ms)
    last=ms[-1]; LY=int(last[:4]); N=int(last[4:])
    years=sorted({int(m[:4]) for m in ms})
    z=lambda:[0]*n
    total={'flight':z(),'pass':z(),'cargo':z()}
    areaPass={a:z() for a in AREAS}
    nat={}; natArea={}; natCargo={}
    for ym,a,na,f,p,c in A:
        if a=='전체 합계': continue
        i=idx[ym]; f,p,c=int(f),int(p),int(c)
        total['flight'][i]+=f; total['pass'][i]+=p; total['cargo'][i]+=c
        areaPass[a][i]+=p
        nat.setdefault(na,z())[i]+=p; natArea[na]=a
        natCargo.setdefault(na,z())[i]+=c
    dom={'flight':z(),'pass':z(),'cargo':z()}
    for ym,rt,f,p,c in Dm:
        i=idx[ym]; dom['flight'][i]+=int(f); dom['pass'][i]+=int(p); dom['cargo'][i]+=int(c)
    city={}
    for ym,c,f,p,cg in C:
        city.setdefault(c,z())[idx[ym]]+=int(p)
    def ytdsum(arr,y): return sum(arr[idx['%d%02d'%(y,m)]] for m in range(1,N+1) if '%d%02d'%(y,m) in idx)
    natorder=sorted(nat,key=lambda k:(AREAS.index(natArea[k]),k))
    natMonthly={k:nat[k] for k in natorder}
    topNations=[dict(name=k,area=natArea[k],**{'y%d'%y:ytdsum(nat[k],y) for y in years}) for k in natorder]
    topNations.sort(key=lambda x:-x['y%d'%LY])
    topCities=[]
    for k,arr in city.items():
        o={'name':k,'v':ytdsum(arr,LY),'p':ytdsum(arr,LY-1),'p23':ytdsum(arr,years[0])}
        o.update({'y%d'%y:ytdsum(arr,y) for y in years})
        topCities.append(o)
    topCities.sort(key=lambda x:(-x['v'],-x['p']))
    ytd={str(y):{'pass':ytdsum(total['pass'],y),'flight':ytdsum(total['flight'],y),'cargo':ytdsum(total['cargo'],y),'dpass':ytdsum(dom['pass'],y),'dflight':ytdsum(dom['flight'],y)} for y in years}
    areaYtd={a:{str(y):ytdsum(areaPass[a],y) for y in years} for a in AREAS}
    areaCargoYtd={a:{str(y):sum(ytdsum(natCargo[k],y) for k in natCargo if natArea[k]==a) for y in years} for a in AREAS}
    drivers={}
    for a in AREAS:
        delta=areaYtd[a][str(LY)]-areaYtd[a][str(LY-1)]
        merged={}
        for k,arr in city.items():
            if city_area(k)!=a: continue
            kk=MERGE.get(k,k)
            merged[kk]=[x+y for x,y in zip(merged.get(kk,[0]*n),arr)]
        ds=[(k.rsplit('(',1)[0],ytdsum(arr,LY)-ytdsum(arr,LY-1)) for k,arr in merged.items()]
        sumd=sum(d for _,d in ds)
        ds=[x for x in ds if (x[1]>0 if delta>=0 else x[1]<0)]
        ds.sort(key=lambda x:-abs(x[1]))
        top=[{'name':nm,'d':d} for nm,d in ds[:3]]
        drivers[a]={'delta':delta,'cover':round(sumd/delta*100) if delta else 0,'top':top,'share':round(sum(t['d'] for t in top)/delta*100) if delta else 0}
    return {'months':ms,'total':total,'domestic':dom,'areas':AREAS,'areaPass':areaPass,'topNations':topNations,'topCities':topCities,
            'natMonthly':natMonthly,'ytd':ytd,'areaYtd':areaYtd,'areaCargoYtd':areaCargoYtd,'drivers':drivers,'cityMonthly':city}
