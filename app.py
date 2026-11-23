import os, sqlite3, secrets, time, json, re, requests
from urllib.parse import urlparse
from flask import Flask, request, session, redirect, url_for, render_template, abort, jsonify
from werkzeug.security import check_password_hash
from functools import wraps

app=Flask(__name__)
secret=os.environ.get('SECRET_KEY','')
if len(secret)<32: raise RuntimeError('Set SECRET_KEY to a random value of at least 32 characters')
app.secret_key=secret
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE','1')=='1',MAX_CONTENT_LENGTH=100*1024*1024,PERMANENT_SESSION_LIFETIME=3600*8)
DB=os.environ.get('LYRICS_DB','/data/lyrics.sqlite3')
os.makedirs(os.path.dirname(os.path.abspath(DB)),exist_ok=True)
def db():
 c=sqlite3.connect(DB, timeout=20);c.row_factory=sqlite3.Row;return c
with db() as c:
 c.execute('CREATE TABLE IF NOT EXISTS songs (id INTEGER PRIMARY KEY, title TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT "", lyrics TEXT NOT NULL, source TEXT NOT NULL DEFAULT "", updated TEXT DEFAULT CURRENT_TIMESTAMP)')
def detect_language(lyrics, key=''):
 if key.startswith('muktisukh:') and key.rsplit(':',1)[-1] in ('gu','hi','en'):
  return key.rsplit(':',1)[-1]
 counts={'gu':0,'hi':0,'en':0}
 for ch in lyrics:
  n=ord(ch)
  if 0x0A80<=n<=0x0AFF:counts['gu']+=1
  elif 0x0900<=n<=0x097F:counts['hi']+=1
  elif ('a'<=ch.lower()<='z') and len(ch)==1:counts['en']+=1
 winner=max(counts,key=counts.get)
 return winner if counts[winner]>=4 else 'unknown'
LANGS={'gu':'Gujarati script','hi':'Hindi / Devanagari script','en':'English / Roman script','unknown':'Unclassified'}
app.jinja_env.globals['LANGS']=LANGS
def csrf():
 if 'csrf' not in session: session['csrf']=secrets.token_urlsafe(32)
 return session['csrf']
app.jinja_env.globals['csrf']=csrf
@app.before_request
def protect():
 if request.method=='POST' and (not session.get('csrf') or not secrets.compare_digest(session['csrf'],request.form.get('csrf',''))): abort(403)
@app.after_request
def headers(r):
 r.headers['Cache-Control']='no-store';r.headers['X-Content-Type-Options']='nosniff';r.headers['X-Frame-Options']='DENY';r.headers['Referrer-Policy']='no-referrer';return r
def login_required(f):
 @wraps(f)
 def inner(*a,**k):
  if not session.get('user'):return redirect(url_for('login'))
  return f(*a,**k)
 return inner
# The deployment supplies two distinct username/password hashes, never credentials in source.
def accounts():
 return {os.environ.get('OWNER_USER','owner'):os.environ.get('OWNER_PASSWORD_HASH',''),os.environ.get('HOST_USER','host'):os.environ.get('HOST_PASSWORD_HASH','')}
# Per-process throttle; deploy a single instance and use a proxy/WAF for production protection.
attempts={}
@app.route('/login',methods=['GET','POST'])
def login():
 error=''
 if request.method=='POST':
  ip=request.remote_addr or 'unknown';now=time.time(); recent=[t for t in attempts.get(ip,[]) if now-t<900];attempts[ip]=recent
  if len(recent)>=10: error='Too many attempts. Try again in 15 minutes.'
  else:
   username=request.form.get('username','').strip(); password=request.form.get('password',''); hashed=accounts().get(username,'')
   try: valid=bool(hashed) and check_password_hash(hashed,password)
   except ValueError: valid=False
   if valid:
    session.clear();session['user']=username;session.permanent=True;attempts.pop(ip,None);return redirect('/')
   recent.append(now);error='Incorrect username or password.'
 return render_template('login.html',error=error)
@app.post('/logout')
def logout():session.clear();return redirect('/login')
@app.get('/')
@login_required
def index():
 q=request.args.get('q','').strip();lang=request.args.get('language','');lang=lang if lang in LANGS else ''
 with db() as c:
  songs=c.execute("SELECT id,title,aliases,source,updated,language FROM songs WHERE (title LIKE ? OR aliases LIKE ? OR lyrics LIKE ?) AND (? = '' OR language = ?) ORDER BY title LIMIT 500",tuple(['%'+q+'%']*3)+(lang,lang)).fetchall()
  if q and len(songs)<40:
   # Flexible spelling: match title words independent of punctuation, order and common vowel variations.
   import re, difflib, unicodedata
   def norm(x):return ''.join(ch.casefold() if ch.isalnum() or unicodedata.category(ch).startswith('M') else ' ' for ch in x)
   def skeleton(x):return re.sub(r'[aeiouh]','',x)
   terms=[x for x in norm(q).split() if len(x)>1]
   seen={r['id'] for r in songs};extra=[]
   for row in c.execute("SELECT id,title,aliases,source,updated,language FROM songs WHERE (? = '' OR language = ?) ORDER BY title",(lang,lang)):
    if row['id'] in seen:continue
    words=norm(row['title']+' '+row['aliases']).split()
    if terms and all(any(t in w or w in t or (len(t)>=4 and len(w)>=4 and skeleton(t)==skeleton(w)) for w in words) for t in terms):extra.append(row)
   songs=list(songs)+extra[:max(0,500-len(songs))]
 return render_template('index.html',songs=songs,q=q,language=lang,user=session['user'])
@app.route('/song/new',methods=['GET','POST'])
@app.route('/song/<int:song_id>',methods=['GET','POST'])
@login_required
def song(song_id=None):
 with db() as c:
  row=c.execute('SELECT * FROM songs WHERE id=?',(song_id,)).fetchone() if song_id else None
  if song_id and not row:abort(404)
  if request.method=='POST':
   title=request.form.get('title','').strip();aliases=request.form.get('aliases','').strip();lyrics=request.form.get('lyrics','').strip();source=request.form.get('source','').strip();language=request.form.get('language','auto')
   if language not in LANGS:language=detect_language(lyrics, (row['import_key'] or '') if row else '')
   if not title or not lyrics: return render_template('song.html',song=dict(title=title,aliases=aliases,lyrics=lyrics,source=source,language=language),error='Title and lyrics are required.')
   if source and (urlparse(source).scheme!='https' or not urlparse(source).netloc):return render_template('song.html',song=dict(title=title,aliases=aliases,lyrics=lyrics,source=source,language=language),error='Source link must begin with https://')
   if song_id:c.execute('UPDATE songs SET title=?,aliases=?,lyrics=?,source=?,language=?,updated=CURRENT_TIMESTAMP WHERE id=?',(title,aliases,lyrics,source,language,song_id))
   else:song_id=c.execute('INSERT INTO songs(title,aliases,lyrics,source,language) VALUES(?,?,?,?,?)',(title,aliases,lyrics,source,language)).lastrowid
   return redirect(url_for('song',song_id=song_id))
  return render_template('song.html',song=row,error='')
@app.post('/song/<int:song_id>/delete')
@login_required
def delete(song_id):
 with db() as c:c.execute('DELETE FROM songs WHERE id=?',(song_id,))
 return redirect('/')
@app.get('/health')
def health():return 'OK'

def fm_tracks(album):
 r=requests.post('https://dbapi.dadabhagwan.org/api/audios/playaudio',json={'view':'spiritualsongs','alang':album,'aname':'','rdate':''},timeout=(5,20),allow_redirects=False)
 r.raise_for_status();return r.json().get('result',{}).get('audiotracks',[])
def fm_lyrics(url):
 u=urlparse(url)
 if u.scheme!='https' or u.hostname!='download.dadabhagwan.org' or not (u.path.startswith('/Bhakti_pad/Lyrics/') or u.path.startswith('/Kids/Bhakti_pad/Lyrics/')) or not u.path.lower().endswith('.txt') or u.username or u.password or u.port not in (None,443):raise ValueError('Unsupported lyrics link')
 with requests.get(url,timeout=(5,8),stream=True,allow_redirects=False) as r:
  if r.status_code!=200:raise ValueError('Lyric file unavailable')
  b=b''
  for chunk in r.iter_content(8192):
   b+=chunk
   if len(b)>200000:raise ValueError('Lyric file too large')
  
  try:return b.decode('utf-8-sig').strip()
  except UnicodeDecodeError:return b.decode('cp1252').strip()
@app.route('/fm',methods=['GET','POST'])
@login_required
def fm():
 tracks=[];error='';album=request.values.get('album','').strip()[:160]
 if album:
  try:
   tracks=fm_tracks(album)
   if not isinstance(tracks,list):raise ValueError('Unexpected response')
  except (requests.RequestException,ValueError,KeyError):error='Could not load that album. Check its name or try again later.';tracks=[]
 return render_template('fm.html',tracks=tracks,album=album,error=error)
@app.post('/fm/preview')
@login_required
def fm_preview():
 album=request.form.get('album','').strip()[:160];track_id=request.form.get('track_id','');error='';song=None
 try:
  tracks=fm_tracks(album);track=next((x for x in tracks if str(x.get('pad_id'))==track_id),None)
  if not track:raise ValueError('Track not found')
  text=fm_lyrics(track.get('Lyrics',''))
  if not text:raise ValueError('No lyrics found')
  song={'title':track.get('TrackName',''),'aliases':'','lyrics':text,'source':track.get('Share_Url','')}
 except (requests.RequestException,ValueError,KeyError,TypeError):error='Lyrics could not be loaded for this track. It may not have an accessible text file.'
 return render_template('song.html',song=song,error=error,imported=True)

# Imports run in one background thread. Each source record has a stable key; reruns
# add missing records but never overwrite a host's edits.
import threading, xml.etree.ElementTree as ET
from urllib.parse import unquote_plus
from flask import send_file
with db() as c:
 cols={r[1] for r in c.execute('PRAGMA table_info(songs)')}
 if 'import_key' not in cols:c.execute('ALTER TABLE songs ADD COLUMN import_key TEXT')
 if 'language' not in cols:c.execute("ALTER TABLE songs ADD COLUMN language TEXT NOT NULL DEFAULT ''")
 c.execute('CREATE UNIQUE INDEX IF NOT EXISTS import_key_unique ON songs(import_key) WHERE import_key IS NOT NULL')
 c.execute('CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
 import gzip
 for marker,filename in [('muktisukh_seed_v1','muktisukh-seed.json.gz'),('fm_seed_v1','fm-seed.json.gz')]:
  if not c.execute('SELECT 1 FROM app_meta WHERE key=?',(marker,)).fetchone():
   seed_path=os.path.join(os.path.dirname(__file__),filename)
   if os.path.exists(seed_path):
    with gzip.open(seed_path,'rt',encoding='utf-8') as f: seed=json.load(f)
    c.executemany('INSERT OR IGNORE INTO songs(title,aliases,lyrics,source,import_key,language) VALUES(?,?,?,?,?,?)',[(x['title'],x['aliases'],x['lyrics'],x['source'],x['import_key'],detect_language(x['lyrics'],x['import_key'])) for x in seed])
    c.execute('INSERT INTO app_meta(key,value) VALUES(?,?)',(marker,str(len(seed))))

with db() as c:
 for row in c.execute("SELECT id,lyrics,import_key FROM songs WHERE language=''").fetchall():
  c.execute('UPDATE songs SET language=? WHERE id=?',(detect_language(row['lyrics'],row['import_key'] or ''),row['id']))

job={'running':False,'status':'Not started','added':0,'skipped':0,'failed':0};job_lock=threading.Lock()
def save_import(key,title,aliases,lyrics,source):
 if not title or not lyrics.strip():return False
 with db() as c:
  cur=c.execute('INSERT OR IGNORE INTO songs(title,aliases,lyrics,source,import_key,language) VALUES(?,?,?,?,?,?)',(title,aliases,lyrics,source,key,detect_language(lyrics,key)))
  return cur.rowcount==1
def has_key(key):
 with db() as c:return c.execute('SELECT 1 FROM songs WHERE import_key=?',(key,)).fetchone() is not None
def mark(result):job[result]+=1
def do_mukti():
 token='';page=0
 while True:
  params={'pageSize':50}
  if token:params['pageToken']=token
  r=requests.get('https://firestore.googleapis.com/v1/projects/kaviraj-aa37f/databases/(default)/documents/pads',params=params,timeout=(8,45));r.raise_for_status();data=r.json();page+=1
  for doc in data.get('documents',[]):
   try:
    f=doc.get('fields',{}); did=doc['name'].rsplit('/',1)[-1];names=f.get('displayTitle',{}).get('mapValue',{}).get('fields',{});ls=f.get('lyrics',{}).get('mapValue',{}).get('fields',{});alltitles=[v.get('stringValue','') for v in names.values() if v.get('stringValue')]
    for lang in ('gu','en','hi'):
     text=ls.get(lang,{}).get('stringValue','').strip();title=names.get(lang,{}).get('stringValue','') or (alltitles[0] if alltitles else did)
     if not text:continue
     key='muktisukh:'+did+':'+lang
     if has_key(key):mark('skipped');continue
     if save_import(key,title+' ['+lang.upper()+']',' | '.join(alltitles),text,'https://www.muktisukh.org/'):mark('added')
   except (KeyError,TypeError,ValueError):mark('failed')
  job['status']=f'Muktisukh: page {page}, {job["added"]} added';token=data.get('nextPageToken','')
  if not token:break
  time.sleep(.4)
def do_fm():
 r=requests.get('https://www.dadabhagwan.fm/sitemap.xml',timeout=(8,60));r.raise_for_status()
 if len(r.content)>15000000:raise ValueError('Sitemap exceeds safe size')
 root=ET.fromstring(r.content);albums=set()
 for node in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc'):
  path=urlparse(node.text or '').path.strip('/').split('/')
  if len(path)>=3 and path[0].lower()=='spiritualsongs':albums.add(unquote_plus(path[1]))
 for i,album in enumerate(sorted(albums)):
  job['status']=f'FM album {i+1} of {len(albums)}: {album}'
  try:
   tracks=fm_tracks(album)
   for track in tracks:
    url=track.get('Lyrics','');key='fm:'+str(track.get('pad_id',''))+':'+url
    if not url or not track.get('pad_id'):mark('skipped');continue
    if has_key(key):mark('skipped');continue
    try:
     text=fm_lyrics(url)
     if text and save_import(key,track.get('TrackName',''),album,text,track.get('Share_Url','')):mark('added')
     else:mark('skipped')
    except (requests.RequestException,ValueError,UnicodeError):mark('failed')
    time.sleep(.2)
  except (requests.RequestException,ValueError,KeyError,TypeError):mark('failed')
  time.sleep(.4)
def run_import(source):
 try:
  if source=='muktisukh':do_mukti()
  elif source=='satsang':do_satsang()
  else:do_fm()
  job['status']='Finished. Check counts; missing or failed tracks require review.'
 except Exception as e:job['status']='Stopped: '+str(e)[:200]+'. Retry to continue; saved entries are kept.';mark('failed')
 finally:job['running']=False
@app.route('/import',methods=['GET','POST'])
@login_required
def import_page():
 if request.method=='POST':
  source=request.form.get('source')
  if source not in ('fm','muktisukh','satsang'):abort(400)
  with job_lock:
   if not job['running']:
    job.update(running=True,status='Starting '+source,added=0,skipped=0,failed=0)
    threading.Thread(target=run_import,args=(source,),daemon=True).start()
  return redirect('/import')
 return render_template('import.html',job=dict(job))
@app.get('/backup.json')
@login_required
def backup():
 with db() as c:rows=[dict(x) for x in c.execute('SELECT title,aliases,lyrics,source,import_key,language FROM songs ORDER BY id')]
 from flask import Response
 return Response(json.dumps({'version':1,'songs':rows},ensure_ascii=False),mimetype='application/json',headers={'Content-Disposition':'attachment; filename="dmht-lyrics-backup.json"'})
@app.post('/restore')
@login_required
def restore():
 f=request.files.get('backup')
 if not f:abort(400)
 try:
  payload=json.load(f);songs=payload['songs']
  if payload.get('version')!=1 or not isinstance(songs,list) or len(songs)>20000:raise ValueError()
  inserted=0;skipped=0
  with db() as c:
   for x in songs:
    title=x.get('title');lyrics=x.get('lyrics');aliases=x.get('aliases','');source=x.get('source','');key=x.get('import_key');language=x.get('language','');language=language if language in LANGS else detect_language(lyrics or '',key or '')
    if not all(isinstance(v,str) for v in (title,lyrics,aliases,source)) or not title or len(lyrics)>200000 or (key is not None and not isinstance(key,str)):raise ValueError()
    if key:
     cur=c.execute('INSERT OR IGNORE INTO songs(title,aliases,lyrics,source,import_key,language) VALUES(?,?,?,?,?,?)',(title,aliases,lyrics,source,key,language));inserted+=cur.rowcount;skipped+=1-cur.rowcount
    elif not c.execute('SELECT 1 FROM songs WHERE import_key IS NULL AND title=? AND lyrics=?',(title,lyrics)).fetchone():
     c.execute('INSERT INTO songs(title,aliases,lyrics,source,language) VALUES(?,?,?,?,?)',(title,aliases,lyrics,source,language));inserted+=1
    else:skipped+=1
  return f'Restored {inserted} entries; skipped {skipped} duplicates. <a href="/">Back to library</a>'
 except (ValueError,TypeError,KeyError,json.JSONDecodeError):return 'Invalid backup. No entries restored.',400

@app.get('/jss')
@login_required
def jss_search():
 from urllib.parse import quote
 q=request.args.get('q','').strip()[:120];lang=request.args.get('language','');lang=lang if lang in ('en','hi','gu') else '';results=[];error=''
 if q:
  try:
   headers={'User-Agent':'Mozilla/5.0 (compatible; DMHT-Lyrics-Library/1.0)','Accept':'text/html,application/xhtml+xml','Accept-Language':'en-US,en;q=0.9'}
   params={'keyword':q}
   if lang:params['blang']={'en':'1','hi':'2','gu':'3'}[lang]
   r=requests.get('https://www.jssvitragvignan.org/audio-pads',params=params,headers=headers,timeout=(5,20),allow_redirects=False);r.raise_for_status()
   if len(r.content)>3000000:raise ValueError('Response too large')
   match=re.search(r'var\s+audioData\s*=\s*(\[.*?\]);',r.text,re.S)
   if not match:raise ValueError('Unexpected page format')
   records=json.loads(match.group(1))
   for x in records[:15]:
    name=x.get('name','');file=x.get('pdf_file','');pdf=('https://www.jssvitragvignan.org/media/audio-pdf/'+quote(file,safe='')) if file else ''
    results.append({'name':name.replace('||',' — '),'pdf':pdf,'language':{'1':'English','2':'Hindi','3':'Gujarati'}.get(str(x.get('languageName')),'Unspecified')})
  except (requests.RequestException,ValueError,TypeError,KeyError):error='JSS search is unavailable right now. Try again or open the source site.'
 return render_template('jss.html',q=q,language=lang,results=results,error=error)

def do_satsang():
 r=requests.post('https://dbapi.dadabhagwan.org/api/audios/pado',json={'langid':0,'album':0,'track':'','orderby':'dateDesc'},timeout=(5,30),allow_redirects=False);r.raise_for_status()
 tracks=r.json().get('result',{}).get('pado',[])
 if not isinstance(tracks,list):raise ValueError('Unexpected Satsang response')
 for i,track in enumerate(tracks):
  name=track.get('Pad_Name','');url=track.get('Lyrics','');pid=track.get('Pad_Id');key='fm:'+str(pid)+':'+url
  job['status']=f'Satsang: track {i+1} of {len(tracks)}: {name}'
  if not url or not pid:mark('skipped');continue
  if has_key(key):mark('skipped');continue
  try:
   text=fm_lyrics(url);aliases=' | '.join(filter(None,[track.get('Pad_Alias_Name',''),track.get('Pad_Album_Name','')]))
   if text and save_import(key,name,aliases,text,track.get('Share_Url','')):mark('added')
   else:mark('skipped')
  except (requests.RequestException,ValueError,UnicodeError):mark('failed')
  time.sleep(.2)
