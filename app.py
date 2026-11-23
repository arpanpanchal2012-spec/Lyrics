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
 q=request.args.get('q','').strip();
 with db() as c: songs=c.execute('SELECT id,title,aliases,source,updated FROM songs WHERE title LIKE ? OR aliases LIKE ? OR lyrics LIKE ? ORDER BY title LIMIT 500',tuple(['%'+q+'%']*3)).fetchall()
 return render_template('index.html',songs=songs,q=q,user=session['user'])
@app.route('/song/new',methods=['GET','POST'])
@app.route('/song/<int:song_id>',methods=['GET','POST'])
@login_required
def song(song_id=None):
 with db() as c:
  row=c.execute('SELECT * FROM songs WHERE id=?',(song_id,)).fetchone() if song_id else None
  if song_id and not row:abort(404)
  if request.method=='POST':
   title=request.form.get('title','').strip();aliases=request.form.get('aliases','').strip();lyrics=request.form.get('lyrics','').strip();source=request.form.get('source','').strip()
   if not title or not lyrics: return render_template('song.html',song=dict(title=title,aliases=aliases,lyrics=lyrics,source=source),error='Title and lyrics are required.')
   if source and (urlparse(source).scheme!='https' or not urlparse(source).netloc):return render_template('song.html',song=dict(title=title,aliases=aliases,lyrics=lyrics,source=source),error='Source link must begin with https://')
   if song_id:c.execute('UPDATE songs SET title=?,aliases=?,lyrics=?,source=?,updated=CURRENT_TIMESTAMP WHERE id=?',(title,aliases,lyrics,source,song_id))
   else:song_id=c.execute('INSERT INTO songs(title,aliases,lyrics,source) VALUES(?,?,?,?)',(title,aliases,lyrics,source)).lastrowid
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
 if u.scheme!='https' or u.hostname!='download.dadabhagwan.org' or not u.path.startswith('/Bhakti_pad/Lyrics/') or not u.path.lower().endswith('.txt') or u.username or u.password or u.port not in (None,443):raise ValueError('Unsupported lyrics link')
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
 c.execute('CREATE UNIQUE INDEX IF NOT EXISTS import_key_unique ON songs(import_key) WHERE import_key IS NOT NULL')
job={'running':False,'status':'Not started','added':0,'skipped':0,'failed':0};job_lock=threading.Lock()
def save_import(key,title,aliases,lyrics,source):
 if not title or not lyrics.strip():return False
 with db() as c:
  cur=c.execute('INSERT OR IGNORE INTO songs(title,aliases,lyrics,source,import_key) VALUES(?,?,?,?,?)',(title,aliases,lyrics,source,key))
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
  else:do_fm()
  job['status']='Finished. Check counts; missing or failed tracks require review.'
 except Exception as e:job['status']='Stopped: '+str(e)[:200]+'. Retry to continue; saved entries are kept.';mark('failed')
 finally:job['running']=False
@app.route('/import',methods=['GET','POST'])
@login_required
def import_page():
 if request.method=='POST':
  source=request.form.get('source')
  if source not in ('fm','muktisukh'):abort(400)
  with job_lock:
   if not job['running']:
    job.update(running=True,status='Starting '+source,added=0,skipped=0,failed=0)
    threading.Thread(target=run_import,args=(source,),daemon=True).start()
  return redirect('/import')
 return render_template('import.html',job=dict(job))
@app.get('/backup.json')
@login_required
def backup():
 with db() as c:rows=[dict(x) for x in c.execute('SELECT title,aliases,lyrics,source,import_key FROM songs ORDER BY id')]
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
    title=x.get('title');lyrics=x.get('lyrics');aliases=x.get('aliases','');source=x.get('source','');key=x.get('import_key')
    if not all(isinstance(v,str) for v in (title,lyrics,aliases,source)) or not title or len(lyrics)>200000 or (key is not None and not isinstance(key,str)):raise ValueError()
    if key:
     cur=c.execute('INSERT OR IGNORE INTO songs(title,aliases,lyrics,source,import_key) VALUES(?,?,?,?,?)',(title,aliases,lyrics,source,key));inserted+=cur.rowcount;skipped+=1-cur.rowcount
    elif not c.execute('SELECT 1 FROM songs WHERE import_key IS NULL AND title=? AND lyrics=?',(title,lyrics)).fetchone():
     c.execute('INSERT INTO songs(title,aliases,lyrics,source) VALUES(?,?,?,?)',(title,aliases,lyrics,source));inserted+=1
    else:skipped+=1
  return f'Restored {inserted} entries; skipped {skipped} duplicates. <a href="/">Back to library</a>'
 except (ValueError,TypeError,KeyError,json.JSONDecodeError):return 'Invalid backup. No entries restored.',400
