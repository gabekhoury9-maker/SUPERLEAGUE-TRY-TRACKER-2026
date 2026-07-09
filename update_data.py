"""Backfill and update the Betfred Super League try tracker.

Online source:
  python update_data.py --season 2026 --source rlp

Manual fallback:
  python update_data.py --import-csv data/incoming_results_template.csv

The online path discovers every completed league match on Rugby League Project,
adds only unseen matches, parses the scoresheet and team sheet, classifies the
try scorer's position, and rebuilds the standalone dashboard and CSV exports.
"""
from __future__ import annotations
import argparse, hashlib, re, time, unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup

from super_league_tracker.core import POSITION_ORDER, bool_series
from super_league_tracker.render import refresh_outputs, short

ROOT=Path(__file__).resolve().parent; DATA=ROOT/'data'
BASE='https://www.rugbyleagueproject.org'
START_POS=['FB','RW','RC','LC','LW','FE','HLF','PR','HK','PR','L2R','R2R','LK']
VALID_POS=set(POSITION_ORDER)
TEAM_CANON={
 'York Knights':'York','York':'York','Hull Kingston Rovers':'Hull KR','Hull KR':'Hull KR',
 'Catalans Dragons':'Catalans','Catalans':'Catalans','Huddersfield Giants':'Huddersfield','Huddersfield':'Huddersfield',
 'Leigh Leopards':'Leigh','Leigh':'Leigh','Leeds Rhinos':'Leeds','Leeds':'Leeds',
 'Warrington Wolves':'Warrington','Warrington':'Warrington','Wigan Warriors':'Wigan','Wigan':'Wigan',
 'Castleford Tigers':'Castleford','Castleford':'Castleford','Bradford Bulls':'Bradford','Bradford':'Bradford',
 'Toulouse Olympique':'Toulouse','Toulouse':'Toulouse','Wakefield Trinity':'Wakefield Trinity',
 'St Helens':'St Helens','Hull FC':'Hull FC','Salford Red Devils':'Salford','Salford':'Salford'
}
PROFILE_MAP={'Fullback':'FB','Five-eighth':'FE','Halfback':'HLF','Hooker':'HK','Front Row':'PR','Front row':'PR',
             'Lock':'LK','Wing':'LW','Centre':'RC','Second Row':'L2R','Second row':'L2R'}
SIDE_DEFAULT={'Wing','Centre','Second Row','Second row'}


def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    s=re.sub(r'\s*\(c\)\s*',' ',s); s=re.sub(r'[^a-z0-9]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()

def player_key(name):
    toks=norm(name).split()
    return toks[-1] if toks else ''

def canon_team(name): return TEAM_CANON.get(re.sub(r'\s+',' ',str(name).strip()),re.sub(r'\s+',' ',str(name).strip()))

def as_int(x,default=1):
    m=re.search(r'\d+',str(x or ''))
    return int(m.group()) if m else default

def get_session():
    s=requests.Session(); s.headers.update({'User-Agent':'Mozilla/5.0 (compatible; SuperLeagueTryTracker/1.0; research dashboard)'})
    return s

def fetch(session,url):
    last=None
    for delay in (0,1,3):
        if delay: time.sleep(delay)
        try:
            r=session.get(url,timeout=45); r.raise_for_status(); return r
        except Exception as e: last=e
    raise RuntimeError(f'Could not fetch {url}: {last}')

def discover_completed(session,season):
    """Discover completed Super League match pages reliably.

    The RLP ``data.html`` page is the most useful source for this job because
    every row is already restricted to the selected competition and explicitly
    states whether the match is Completed.  Older versions of this tracker used
    only ``results.html`` and could miss links when RLP changed the link label.

    We first collect completed rows from ``data.html``.  If that page layout is
    unavailable, we fall back to all match-detail links on ``results.html`` and
    let :func:`parse_match` reject scheduled or non-league fixtures.
    """
    data_url=f'{BASE}/seasons/super-league-{season}/data.html'
    soup=BeautifulSoup(fetch(session,data_url).text,'lxml')
    found=[]
    for a in soup.find_all('a',href=True):
        href=urljoin(data_url,a['href'])
        is_match_id=bool(re.search(r'/matches/\d+/?$',href))
        is_summary=(f'/seasons/super-league-{season}/round-' in href and href.endswith('/summary.html'))
        if not (is_match_id or is_summary):
            continue
        row=a.find_parent('tr')
        row_text=' '.join(row.stripped_strings) if row else ' '.join(a.parent.stripped_strings)
        if re.search(r'\bCompleted\b',row_text,re.I):
            found.append(href)
    found=list(dict.fromkeys(found))
    if found:
        return found

    results_url=f'{BASE}/seasons/super-league-{season}/results.html'
    soup=BeautifulSoup(fetch(session,results_url).text,'lxml')
    for a in soup.find_all('a',href=True):
        href=urljoin(results_url,a['href'])
        is_match_id=bool(re.search(r'/matches/\d+/?$',href))
        is_summary=(f'/seasons/super-league-{season}/round-' in href and href.endswith('/summary.html'))
        if is_match_id or is_summary:
            found.append(href)
    return list(dict.fromkeys(found))

def parse_score_header(soup):
    table=soup.find('table',class_='program')
    if table:
        try:
            body=table.find_all('tbody')[0].find_all('tr')[1]
            th=body.find_all('th',recursive=False)
            l=th[0].find_all('td'); r=th[2].find_all('td')
            return canon_team(l[0].get_text(' ',strip=True)),as_int(l[1].get_text()),canon_team(r[1].get_text(' ',strip=True)),as_int(r[0].get_text())
        except Exception: pass
    for h in soup.find_all(['h2','h3']):
        t=' '.join(h.stripped_strings)
        m=re.match(r'(.+?)\s+(\d+)\s+(?:def\.|defeated|drew with)\s+(.+?)\s+(\d+)$',t,re.I)
        if m: return canon_team(m.group(1)),int(m.group(2)),canon_team(m.group(3)),int(m.group(4))
    raise ValueError('Could not parse teams and score')

def parse_info(soup,label):
    body=soup.find('tbody',id='match_info')
    if body:
        for tr in body.find_all('tr',recursive=False):
            th=tr.find('th'); td=tr.find('td')
            if th and td and th.get_text(' ',strip=True)==label: return td.get_text(' ',strip=True)
    text=soup.get_text('\n',strip=True)
    m=re.search(rf'{re.escape(label)}\s+([^\n]+)',text)
    return m.group(1).strip() if m else ''

def parse_round(soup):
    text=soup.get_text(' ',strip=True)
    m=re.search(r'\bRound\s+(\d+)\b',text)
    if m: return f'Round {m.group(1)}',int(m.group(1)),False
    for label in ['Grand Final','Semi Final','Eliminator','Qualifying Final','Final']:
        if re.search(rf'\b{re.escape(label)}\b',text,re.I): return label,99,True
    raise ValueError('Could not determine round')

def parse_match_id(soup,url):
    a=soup.find('a',href=re.compile(r'/matches/\d+'))
    target=a.get('href','') if a else url
    m=re.search(r'/matches/(\d+)',target)
    if m: return int(m.group(1))
    return int(hashlib.sha1(url.encode()).hexdigest()[:11],16)

def parse_lineups(soup,match_id,season,round_label,round_number,date,home,away,url):
    body=soup.find('tbody',id='match_teams')
    if not body: raise ValueError('No match_teams table')
    rows=[]; current=''; starts=[0,0]
    for tr in body.find_all('tr',recursive=False):
        th=tr.find('th')
        if th:
            x=th.get_text(' ',strip=True)
            if x: current=x
        if current=='HC': continue
        tds=tr.find_all('td',recursive=False)
        if len(tds)<4: continue
        entries=[(0,home,away,tds[0],tds[1]),(1,away,home,tds[3],tds[2])]
        for side,team,opp,name_td,num_td in entries:
            a=name_td.find('a',href=True)
            name=name_td.get_text(' ',strip=True)
            if not a or not name: continue
            raw=current or 'B'; starting=raw!='B'
            pos=''
            if starting and starts[side]<13:
                pos=START_POS[starts[side]]; starts[side]+=1
            pid_match=re.search(r'/players/(\d+)',a['href']); pid=pid_match.group(1) if pid_match else ''
            rows.append({'match_id':match_id,'season':season,'round_label':round_label,'round_number':round_number,'date':date,
              'team':team,'opposition_team':opp,'player_id':pid,'full_name':name,'player_key':player_key(name),
              'jersey_number':as_int(num_td.get_text(),default=0) or '', 'raw_position':raw,'display_position':pos,
              'is_starting':starting,'source_url':url,'player_url':urljoin(url,a['href'])})
    if sum(bool(x['is_starting']) for x in rows) < 24:
        raise ValueError(f'Incomplete lineups: only {sum(bool(x["is_starting"]) for x in rows)} starters')
    return rows

def parse_scorers(soup,home,away):
    body=soup.find('tbody',id='match_scoresheet')
    if not body: return []
    out=[]; current=''
    for tr in body.find_all('tr',recursive=False):
        th=tr.find('th')
        if th and th.get_text(' ',strip=True): current=th.get_text(' ',strip=True)
        if current not in {'T','PT'}: continue
        tds=tr.find_all('td',recursive=False)
        left=tr.select_one('td.name.left a'); right=tr.select_one('td.name:not(.left) a')
        if left:
            count=as_int(tds[1].get_text() if len(tds)>1 else '',1)
            pid=re.search(r'/players/(\d+)',left.get('href',''))
            out.append({'team':home,'opposition_team':away,'full_name':left.get_text(' ',strip=True),'player_id':pid.group(1) if pid else '',
                        'player_key':player_key(left.get_text()),'tries':count,'player_url':urljoin(BASE,left.get('href',''))})
        if right:
            count=as_int(tds[2].get_text() if len(tds)>2 else '',1)
            pid=re.search(r'/players/(\d+)',right.get('href',''))
            out.append({'team':away,'opposition_team':home,'full_name':right.get_text(' ',strip=True),'player_id':pid.group(1) if pid else '',
                        'player_key':player_key(right.get_text()),'tries':count,'player_url':urljoin(BASE,right.get('href',''))})
    return out

def parse_match(session,url,season):
    r=fetch(session,url); url=r.url; soup=BeautifulSoup(r.text,'lxml')
    text=soup.get_text(' ',strip=True)
    if f'{season} Betfred Super League' not in text or 'Status Completed' not in text: return None
    home,hs,away,ascore=parse_score_header(soup); label,rn,is_final=parse_round(soup)
    rawdate=parse_info(soup,'Date'); clean=re.sub(r'(\d+)(st|nd|rd|th)',r'\1',rawdate)
    date=pd.to_datetime(clean,dayfirst=True,errors='raise').date().isoformat()
    mid=parse_match_id(soup,url)
    venue=parse_info(soup,'Venue')
    lineups=parse_lineups(soup,mid,season,label,rn,date,home,away,url)
    scorers=parse_scorers(soup,home,away)
    match={'match_id':mid,'season':season,'round_label':label,'round_number':rn,'date':date,
           'home_team':home,'home_team_score':hs,'away_team':away,'away_team_score':ascore,'is_final':is_final,
           'venue':venue,'source_url':url,'source':'Rugby League Project weekly scrape',
           'home_team_short':short(home),'away_team_short':short(away)}
    return {'match':match,'lineups':lineups,'scorers':scorers}

def load_csv(name): return pd.read_csv(DATA/name)

def profile_role(session,player_url,cache):
    if not player_url: return None,None
    key=player_url
    if key in cache: return cache[key]
    try:
        r=fetch(session,player_url); canonical=r.url
        pos_url=canonical.replace('/summary.html','/positions.html') if '/summary.html' in canonical else canonical.rstrip('/')+'/positions.html'
        soup=BeautifulSoup(fetch(session,pos_url).text,'lxml')
        candidates=[]
        for tr in soup.find_all('tr'):
            cells=[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'],recursive=False)]
            if len(cells)>=2 and cells[0] in PROFILE_MAP:
                candidates.append((as_int(cells[1],0),cells[0]))
        if candidates:
            apps,role=max(candidates); result=(PROFILE_MAP[role],role)
        else: result=(None,None)
    except Exception: result=(None,None)
    cache[key]=result; return result

def classify_scorer(sc, match_lineups, all_lineups, history, overrides, session, profile_cache):
    team=sc['team']; pid=str(sc.get('player_id','')); key=sc['player_key']
    candidates=[x for x in match_lineups if x['team']==team and ((pid and str(x['player_id'])==pid) or (not pid and x['player_key']==key))]
    if not candidates: candidates=[x for x in match_lineups if x['team']==team and x['player_key']==key]
    if candidates:
        row=candidates[0]
        if row['is_starting'] and row['display_position']:
            return row['display_position'],'starting lineup position',False
    # modal current-season starts, keyed first by player id then surname.
    starts=all_lineups[(all_lineups.team==team)&(bool_series(all_lineups.is_starting))]
    if pid and 'player_id' in starts:
        q=starts[starts.player_id.astype(str).eq(pid)]
        if len(q): return q.display_position.value_counts().idxmax(),'interchange; season modal starting position',False
    q=starts[starts.player_key.eq(key)]
    if len(q): return q.display_position.value_counts().idxmax(),'interchange; season modal starting position',False
    q=history[(history.team==team)&(history.player_key==key)]
    if len(q): return q.iloc[0].display_position,'historical modal starting position',False
    q=overrides[(overrides.team==team)&(overrides.player_key==key)]
    if len(q): return q.iloc[0].display_position,'documented role override',False
    purl=candidates[0].get('player_url','') if candidates else sc.get('player_url','')
    pos,role=profile_role(session,purl,profile_cache)
    if pos:
        review=role in SIDE_DEFAULT
        return pos,('RLP player profile role; side default — review recommended' if review else 'RLP player profile primary role'),review
    # Last resort keeps the dashboard complete but is never hidden from the review queue.
    jersey=as_int(candidates[0].get('jersey_number','') if candidates else '',0)
    if jersey==14: pos='HK'
    elif jersey in {15,16,17}: pos='PR'
    else: pos='PR'
    return pos,'bench-number fallback — review recommended',True

def sync_rlp(season,delay=0.18,force_full_backfill=False):
    session=get_session(); matches=load_csv('matches.csv'); events=load_csv('try_events.csv'); lineups=load_csv('lineups.csv')
    history=load_csv('player_role_history.csv'); overrides=load_csv('position_overrides.csv')

    # A result is considered fully enriched only after a final team sheet has
    # been stored.  This prevents score-only preload rows (and any interrupted
    # earlier run) from blocking the scorer/position backfill.
    season_lineups=lineups[lineups.season.astype(str).eq(str(season))] if not lineups.empty else lineups
    enriched_ids=set(pd.to_numeric(season_lineups.match_id,errors='coerce').dropna().astype(int))
    enriched_match_rows=matches[pd.to_numeric(matches.match_id,errors='coerce').isin(enriched_ids)].copy()
    enriched_keys=set(zip(enriched_match_rows.season.astype(str),enriched_match_rows.date.astype(str),
                          enriched_match_rows.home_team.astype(str),enriched_match_rows.away_team.astype(str)))

    urls=discover_completed(session,season)
    parsed=[]; failures=[]; skipped_enriched=0
    for i,url in enumerate(urls,1):
        candidate=re.search(r'/matches/(\d+)',url)
        if candidate and int(candidate.group(1)) in enriched_ids and not force_full_backfill:
            skipped_enriched+=1
            continue
        try:
            item=parse_match(session,url,season)
            if not item: continue
            match=item['match']
            key=(str(match['season']),str(match['date']),str(match['home_team']),str(match['away_team']))
            if not force_full_backfill and (int(match['match_id']) in enriched_ids or key in enriched_keys):
                skipped_enriched+=1
                continue
            parsed.append(item)
            enriched_ids.add(int(match['match_id']))
            enriched_keys.add(key)
        except Exception as e:
            failures.append({'url':url,'error':str(e)})
        time.sleep(delay)

    if not parsed:
        print(f'No unenriched completed {season} Super League matches. Discovered {len(urls)} match links; skipped {skipped_enriched} already enriched.')
        if failures: print(f'{len(failures)} page(s) could not be parsed; see updater_failures.csv.')
        pd.DataFrame(failures).to_csv(DATA/'updater_failures.csv',index=False)
        return {'discovered':len(urls),'enriched':0,'skipped':skipped_enriched,'failures':len(failures)}

    new_lineup_rows=[r for item in parsed for r in item['lineups']]
    incoming_lineups=pd.DataFrame(new_lineup_rows).drop(columns=['player_url'],errors='ignore')
    incoming_ids=set(pd.to_numeric(incoming_lineups.match_id,errors='coerce').dropna().astype(int))
    # Replace any previous partial team sheet for a match being re-enriched.
    keep_lineups=~pd.to_numeric(lineups.match_id,errors='coerce').isin(incoming_ids)
    temp_lineups=pd.concat([lineups[keep_lineups],incoming_lineups],ignore_index=True)

    profile_cache={}; review=[]; new_events=[]
    for item in parsed:
        match=item['match']; ml=item['lineups']
        for sc in item['scorers']:
            pos,method,needs_review=classify_scorer(sc,ml,temp_lineups,history,overrides,session,profile_cache)
            ev={**{k:match[k] for k in ['match_id','season','round_label','round_number','date','is_final']},
                'team':sc['team'],'opposition_team':sc['opposition_team'],'player_id':sc['player_id'],'full_name':sc['full_name'],
                'player_key':sc['player_key'],'jersey_number':'','raw_position':'','display_position':pos,'tries':int(sc['tries']),
                'position_method':method,'source_url':match['source_url'],'source':match['source'],
                'team_short':short(sc['team']),'opposition_short':short(sc['opposition_team'])}
            new_events.append(ev)
            if needs_review:
                review.append({'season':season,'match_id':match['match_id'],'team':sc['team'],'player':sc['full_name'],
                  'player_id':sc['player_id'],'raw_position':'B','assigned_position':pos,'reason':method,'source_url':match['source_url']})

    incoming_matches=pd.DataFrame([x['match'] for x in parsed])
    incoming_match_ids=set(pd.to_numeric(incoming_matches.match_id,errors='coerce').dropna().astype(int))
    incoming_keys=set(zip(incoming_matches.season.astype(str),incoming_matches.date.astype(str),
                          incoming_matches.home_team.astype(str),incoming_matches.away_team.astype(str)))
    # Remove both score-only snapshots with the same fixture key and any partial
    # row with the same real RLP match id before inserting the full record.
    keep_matches=~matches.apply(lambda r:(
        pd.to_numeric(pd.Series([r.get('match_id')]),errors='coerce').fillna(-1).astype(int).iloc[0] in incoming_match_ids
        or (str(r.get('season','')),str(r.get('date','')),str(r.get('home_team','')),str(r.get('away_team',''))) in incoming_keys
    ),axis=1)
    matches=pd.concat([matches[keep_matches],incoming_matches],ignore_index=True)

    # Replace scorer rows for matches being re-enriched, then insert the newly
    # classified events.  This makes manual full-backfill runs idempotent.
    keep_events=~pd.to_numeric(events.match_id,errors='coerce').isin(incoming_match_ids)
    events=pd.concat([events[keep_events],pd.DataFrame(new_events)],ignore_index=True)
    lineups=temp_lineups
    matches=matches.sort_values(['date','match_id']).drop_duplicates('match_id',keep='last')
    events=events.sort_values(['date','match_id','team','full_name']).drop_duplicates(['match_id','team','player_id','full_name'],keep='last')
    lineups=lineups.sort_values(['date','match_id','team','is_starting'],ascending=[True,True,True,False]).drop_duplicates(['match_id','team','player_id','full_name'],keep='last')
    matches.to_csv(DATA/'matches.csv',index=False); events.to_csv(DATA/'try_events.csv',index=False); lineups.to_csv(DATA/'lineups.csv',index=False)
    old_review=load_csv('review_queue.csv') if (DATA/'review_queue.csv').exists() else pd.DataFrame()
    pd.concat([old_review,pd.DataFrame(review)],ignore_index=True).drop_duplicates(['match_id','team','player'],keep='last').to_csv(DATA/'review_queue.csv',index=False)
    pd.DataFrame(failures).to_csv(DATA/'updater_failures.csv',index=False)
    manifest=refresh_outputs(ROOT)
    print(f'Enriched {len(parsed)} completed match(es), {sum(int(x["tries"]) for x in new_events)} tries. {season} total: {manifest["2026_matches"] if season==2026 else len(matches[matches.season==season])} matches.')
    if review: print(f'{len(review)} bench classification(s) were added to data/review_queue.csv for side/role review.')
    if failures: print(f'{len(failures)} page(s) could not be parsed; see data/updater_failures.csv.')
    return {'discovered':len(urls),'enriched':len(parsed),'skipped':skipped_enriched,'failures':len(failures)}

def import_csv(path):
    incoming=pd.read_csv(path)
    required={'match_id','season','round_label','date','home_team','home_team_score','away_team','away_team_score','scoring_team','scorer','tries','display_position'}
    missing=required-set(incoming.columns)
    if missing: raise ValueError(f'Missing columns: {sorted(missing)}')
    bad=set(incoming.display_position.dropna())-VALID_POS
    if bad: raise ValueError(f'Invalid display positions: {sorted(bad)}')
    matches=load_csv('matches.csv'); events=load_csv('try_events.csv')
    for mid,g in incoming.groupby('match_id'):
        r=g.iloc[0]; is_final=str(r.get('is_final','false')).lower() in {'true','1','yes'}
        match={'match_id':int(mid),'season':int(r.season),'round_label':r.round_label,'round_number':pd.to_numeric(r.get('round_number'),errors='coerce'),
          'date':r.date,'home_team':canon_team(r.home_team),'home_team_score':int(r.home_team_score),'away_team':canon_team(r.away_team),
          'away_team_score':int(r.away_team_score),'is_final':is_final,'venue':'','source_url':r.get('source_url','Manual weekly import'),
          'source':'Manual weekly import','home_team_short':short(canon_team(r.home_team)),'away_team_short':short(canon_team(r.away_team))}
        matches=pd.concat([matches,pd.DataFrame([match])],ignore_index=True)
        for _,x in g.iterrows():
            team=canon_team(x.scoring_team); opp=match['away_team'] if team==match['home_team'] else match['home_team']
            ev={'match_id':int(mid),'season':int(x.season),'round_label':x.round_label,'round_number':pd.to_numeric(x.get('round_number'),errors='coerce'),
              'date':x.date,'is_final':is_final,'team':team,'opposition_team':opp,'player_id':'','full_name':x.scorer,'player_key':player_key(x.scorer),
              'jersey_number':x.get('jersey_number',''),'raw_position':'','display_position':x.display_position,'tries':int(x.tries),
              'position_method':x.get('position_method','manual weekly import'),'source_url':x.get('source_url','Manual weekly import'),
              'source':'Manual weekly import','team_short':short(team),'opposition_short':short(opp)}
            events=pd.concat([events,pd.DataFrame([ev])],ignore_index=True)
    matches.drop_duplicates('match_id',keep='last').to_csv(DATA/'matches.csv',index=False)
    events.drop_duplicates(['match_id','team','full_name','display_position'],keep='last').to_csv(DATA/'try_events.csv',index=False)
    refresh_outputs(ROOT); print(f'Imported {incoming.match_id.nunique()} match(es).')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--season',type=int,default=2026); ap.add_argument('--source',choices=['rlp'],default='rlp')
    ap.add_argument('--import-csv',type=Path); ap.add_argument('--delay',type=float,default=0.18); ap.add_argument('--force-full-backfill',action='store_true')
    args=ap.parse_args()
    if args.import_csv: import_csv(args.import_csv)
    else: sync_rlp(args.season,args.delay,args.force_full_backfill)
if __name__=='__main__': main()
