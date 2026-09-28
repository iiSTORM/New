import sys
import json,pandas as pd
d=json.load(open(sys.argv[1] if len(sys.argv)>1 else 'prizepicks-payload.json'))
inc={(x['type'],x['id']):x for x in d['included']}
rows=[]
for p in d['data']:
    a=p['attributes'];r=p['relationships']
    pl=inc.get(('new_player',r['new_player']['data']['id']),{}).get('attributes',{})
    lg=inc.get(('league',r['league']['data']['id']),{}).get('attributes',{})
    rows.append(dict(id=p['id'],player=pl.get('display_name') or pl.get('name'),team=pl.get('team'),pos=pl.get('position'),
      league=lg.get('name'),stat=a['stat_display_name'],line=a['line_score'],odds=a['odds_type'],promo=a['is_promo'],
      flash=a['flash_sale_line_score'],start=a['start_time'],status=a['status'],desc=a['description'],
      adj=a['adjusted_odds'],rank=a.get('rank'),updated=a['updated_at'],combo=pl.get('combo'),trend=a.get('trending_count')))
df=pd.DataFrame(rows)
df['start']=pd.to_datetime(df['start'],utc=True).dt.tz_convert('America/Chicago')
df.to_pickle('pp.pkl'); print(len(df),'props ->', 'pp.pkl')
